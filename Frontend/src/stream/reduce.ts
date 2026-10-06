import type { FoundVideo, RunAgent, RunState, StreamEvent } from "../api/types";

export type AgentStatus = "waiting" | "working" | "done" | "failed";

export interface AgentView {
  id: string;
  role: string;
  platform: string | null;
  status: AgentStatus;
  action: string | null;
  lastAt: string | null;
}

export interface StreamState {
  events: StreamEvent[];
  seen: Set<number>;
  agents: Record<string, AgentView>;
  order: string[];
  saved: FoundVideo[];
  runState: RunState | null;
}

export type Group = "thinking" | "plans" | "tools" | "videos" | "problems" | "status";

const GROUPS: Record<string, Group> = {
  "agent.thought": "thinking", "character.read": "thinking", "trend.studied": "thinking",
  "plan.created": "plans", "round.finished": "plans",
  "agent.tool_call": "tools", "agent.tool_result": "tools", "agent.started": "tools", "agent.finished": "tools",
  "analysis.started": "tools", "analysis.finished": "tools",
  "video.saved": "videos",
  "candidate.rejected": "problems", "error": "problems", "provider.switched": "problems",
  "run.state": "status",
};

export const groupOf = (type: string): Group => GROUPS[type] ?? "status";

const FIRST = ["lead", "reader", "learner"];
const ENDED = ["review_ready", "stopped", "failed"];

export function initialStream(seed: RunAgent[] = []): StreamState {
  const agents: Record<string, AgentView> = {};
  for (const a of seed) {
    agents[a.id] = { id: a.id, role: a.role, platform: a.platform, status: a.status, action: a.goal, lastAt: null };
  }
  return { events: [], seen: new Set(), agents, order: seed.map((a) => a.id), saved: [], runState: null };
}

function argText(args: Record<string, unknown> | undefined): string {
  if (!args) return "";
  const main = args.query ?? args.hashtag ?? args.handle ?? args.url ?? Object.values(args)[0];
  return main == null ? "" : String(main);
}

/** Reasoning summaries arrive as light markdown ("**Title**\n\nText"); the stream shows plain text. */
export const plain = (text: unknown) => String(text ?? "").replace(/\*\*/g, "").replace(/\s+/g, " ").trim();

export function describe(e: StreamEvent): string {
  const p = e.payload;
  switch (e.type) {
    case "agent.thought": return plain(p.text);
    case "agent.tool_call": return `${p.tool}: ${argText(p.args)}`.replace(/: $/, "");
    case "agent.tool_result": return `${p.tool} returned ${String(p.summary ?? "").slice(0, 160)}`;
    case "agent.started": return String(p.goal ?? "started");
    case "agent.finished":
      return p.failed ? `failed: ${p.failed}` : `done: ${p.accepted ?? 0} candidates, ${p.leads ?? 0} leads`;
    case "plan.created": return String(p.reasoning ?? "");
    case "character.read": return `${p.read?.vibe ?? ""} ${(p.read?.possible_niches ?? []).join(", ")}`;
    case "trend.studied": return `${p.study?.format ?? ""} ${p.url ?? ""}`;
    case "candidate.rejected": return `${p.canonical_id ?? ""} ${p.reason ?? ""}`;
    case "analysis.started": return `watching ${p.canonical_id}`;
    case "analysis.finished": return `${p.verdict} ${p.canonical_id} ${p.reason ?? ""}`;
    case "video.saved": return `saved ${p.video?.url ?? ""}`;
    case "provider.switched": return `${p.from} → ${p.to}: ${p.reason ?? ""}`;
    case "error": return String(p.message ?? "");
    case "run.state": return String(p.state ?? "");
    default: return "";
  }
}

function touch(state: StreamState, id: string, role: string, platform: string | null, patch: Partial<AgentView>,
  at: string) {
  const prev = state.agents[id] ?? { id, role, platform, status: "waiting" as AgentStatus, action: null, lastAt: null };
  state.agents[id] = { ...prev, platform: prev.platform ?? platform, ...patch, lastAt: at };
  if (!state.order.includes(id)) state.order.push(id);
}

function applyOne(state: StreamState, e: StreamEvent) {
  const p = e.payload;
  const agent = p.agent as { id: string; role: string; platform?: string | null } | undefined;
  if (e.type === "run.state") {
    state.runState = p.state as RunState;
    if (ENDED.includes(String(p.state))) {
      for (const id of Object.keys(state.agents)) {
        if (state.agents[id].status === "working") state.agents[id] = { ...state.agents[id], status: "done" };
      }
    }
    if (p.state === "planning") touch(state, "lead", "lead", null, { status: "working", action: "planning the round" }, e.created_at);
    if (p.state === "reading_character") touch(state, "reader", "reader", null, { status: "working", action: "looking at the images" }, e.created_at);
    return;
  }
  if (e.type === "video.saved" && p.video && !state.saved.some((v) => v.id === p.video.id)) {
    state.saved = [...state.saved, p.video as FoundVideo];
  }
  if (!agent) return;
  const { id, role } = agent;
  const platform = agent.platform ?? null;
  switch (e.type) {
    case "plan.created":
      touch(state, id, role, platform, { status: "done", action: `planned round ${p.round}` }, e.created_at);
      for (const t of p.tasks ?? []) {
        if (!state.agents[t.task_id]) touch(state, t.task_id, t.role, t.platform, { status: "waiting", action: t.goal }, e.created_at);
      }
      break;
    case "character.read":
      touch(state, id, role, platform, { status: "done", action: "read the character" }, e.created_at);
      break;
    case "agent.started":
    case "analysis.started":
      touch(state, id, role, platform, { status: "working", action: describe(e) }, e.created_at);
      break;
    case "agent.thought":
    case "agent.tool_call":
      touch(state, id, role, platform, { status: "working", action: describe(e).slice(0, 140) }, e.created_at);
      break;
    case "agent.finished":
      touch(state, id, role, platform, { status: p.failed ? "failed" : "done", action: describe(e) }, e.created_at);
      break;
    case "analysis.finished":
      touch(state, id, role, platform, { status: p.verdict === "failed" ? "failed" : "done", action: describe(e) }, e.created_at);
      break;
    default:
      touch(state, id, role, platform, {}, e.created_at);
  }
}

export function applyEvents(prev: StreamState, incoming: StreamEvent[]): StreamState {
  const fresh = incoming.filter((e) => !prev.seen.has(e.id));
  if (fresh.length === 0) return prev;
  const state: StreamState = { ...prev, seen: new Set(prev.seen), agents: { ...prev.agents }, order: [...prev.order] };
  const sorted = [...fresh].sort((a, b) => a.id - b.id);
  for (const e of sorted) {
    if (state.seen.has(e.id)) continue;
    state.seen.add(e.id);
    applyOne(state, e);
  }
  const merged = [...prev.events, ...sorted.filter((e, i, arr) => i === 0 || arr[i - 1].id !== e.id)];
  state.events = merged.sort((a, b) => a.id - b.id);
  const rank = (id: string) => (FIRST.includes(id) ? FIRST.indexOf(id) : FIRST.length);
  state.order.sort((a, b) => rank(a) - rank(b));
  return state;
}

export interface Filters {
  agent?: string | null;
  groups?: Set<Group>;
  query?: string;
}

export function filterEvents(events: StreamEvent[], f: Filters): StreamEvent[] {
  const q = f.query?.trim().toLowerCase();
  return events.filter((e) => {
    if (f.agent && e.payload.agent?.id !== f.agent) return false;
    if (f.groups && f.groups.size > 0 && !f.groups.has(groupOf(e.type))) return false;
    if (q && !describe(e).toLowerCase().includes(q) && !JSON.stringify(e.payload).toLowerCase().includes(q)) return false;
    return true;
  });
}
