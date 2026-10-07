export type Platform = "tiktok" | "instagram" | "youtube" | "x";

export type RunState =
  | "created" | "reading_character" | "studying_trends" | "planning" | "running" | "paused_usage" | "curating"
  | "review_ready" | "stopped" | "failed" | "interrupted";

export interface Character {
  slug: string;
  name: string;
  image_url: string | null;
  images: string[];
  runs: number;
}

export interface CharacterRead {
  look: string;
  vibe: string;
  performance_angle: string;
  possible_niches: string[];
  kling_constraints: string;
  avoid: string;
}

export interface RunAgent {
  id: string;
  role: string;
  platform: Platform | null;
  status: "waiting" | "working" | "done" | "failed";
  goal: string | null;
}

export interface RunDetail {
  id: string;
  state: RunState;
  character: { slug: string; name: string; image_url: string | null };
  stop_reason: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  active: boolean;
  inputs: { freshness?: string; trend_urls?: string[]; targets?: { url: string }[] };
  character_read: CharacterRead | null;
  round: number;
  minutes: number;
  agents: RunAgent[];
  findings: Record<string, number>;
  satisfaction: number | null;
}

export interface RunSummary {
  id: string;
  state: RunState;
  stop_reason: string | null;
  started_at: string;
  finished_at: string | null;
  videos: number;
  satisfaction: number | null;
  active: boolean;
}

export type Rating = "up" | "down";

export interface FoundVideo {
  id: string;
  cluster_id: string | null;
  run_id: string;
  canonical_id: string;
  platform: Platform;
  platform_id: string;
  url: string;
  thumbnail_url: string | null;
  creator: string | null;
  caption: string | null;
  views: number | null;
  posted_at: string | null;
  duration_s: number | null;
  score: number | null;
  scores: { fit: number | null; feasibility: number | null; momentum: number | null; freshness: number | null };
  why: string | null;
  adaptation: string | null;
  best_segment: { start_s: number; end_s: number } | null;
  watch_out: string | null;
  niche_guess: string | null;
  source: "agent" | "owner";
  found_at: string | null;
  feedback: { rating: Rating; note: string | null } | null;
}

export interface StreamEvent {
  id: number;
  type: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: Record<string, any>;
  created_at: string;
}

export interface StartRun {
  character: string;
  platforms: Platform[];
  freshness: "day" | "week" | "month" | "any";
  minutes: number;
  trend_urls: string[];
  targets: { url: string; character: string }[];
}

export interface ProviderStatus {
  provider: string;
  connected: boolean;
  status: string;
  cooling_until: number | null;
}

export interface RunHistoryRow {
  id: string;
  character: { slug: string; name: string; image_url: string | null };
  state: RunState;
  stop_reason: string | null;
  started_at: string;
  finished_at: string | null;
  videos: number;
  satisfaction: number | null;
  tokens: number;
  active: boolean;
}

export interface CharacterDetail {
  slug: string;
  name: string;
  image_url: string | null;
  images: string[];
  latest_read: CharacterRead | null;
  taste_md: string | null;
  runs: number;
  videos_found: number;
}

export interface ProviderUsage {
  provider: string;
  status: "ok" | "cooling" | "auth_error";
  used_percent: number | null;
  window_minutes: number | null;
  resets_at: number | null;
  cooling_until: number | null;
  last_error: string | null;
  tokens_today: number;
  calls_today: number;
  avg_tokens_per_run: number | null;
  windows: { name: string; used_percent: number; window_minutes: number | null; resets_at: number | null }[];
}

/** One item of Settings, Accounts & keys: never a secret, only whether it's set and a masked hint. */
export interface AccountItem {
  id: string;
  label: string;
  kind: "key" | "session";
  set: boolean;
  hint: string | null;
  updated_at: string | null;
  status: "set" | "not set" | "connected" | "expired" | "not connected";
  connecting: { state: "waiting" | "connected" | "failed"; message: string } | null;
}

/** Which subscription every agent tries first; null = each agent's own order, searches alternate. */
export interface ProviderPriority {
  first: string | null;
  providers: string[];
}

export interface ModelChoice {
  models: { id: string; name: string; efforts: string[]; unavailable: string | null }[];
  main: string | null;
  fast: string | null;
  effort: string | null;
  efforts: string[];
}
