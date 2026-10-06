export type RunState =
  | "created" | "planning" | "running" | "paused_usage" | "curating" | "review_ready" | "stopped" | "failed"
  | "interrupted";

export interface CharacterSummary {
  slug: string;
  name: string;
  version: number;
  runs: number;
  niche_open: boolean | null;
  canonical_image_url: string | null;
}

export interface CharacterDetail extends CharacterSummary {
  brief: string;
  sections: Record<string, string>;
  seeds: { url: string; canonical_id: string | null; source: string }[];
  taste_profile: { version: number; body_md: string; author: string; created_at: string } | null;
  directions: { key: string; label: string; niche: string | null; alpha: number; beta: number }[];
}

export interface RunSummary {
  id: string;
  character: string;
  state: RunState;
  round: number;
  stop_reason: string | null;
  error: string | null;
  explore_ratio: number | null;
  settings: Record<string, unknown>;
  started_at: string;
  finished_at: string | null;
  active: boolean;
}

export interface RunTask {
  id: string;
  type: string;
  platform: string | null;
  state: string;
  goal: string | null;
  direction: string | null;
  scope: Record<string, string[] | string>;
  attempts: number;
  result: Record<string, number | string | null>;
}

export interface RunDetail extends RunSummary {
  rounds: { number: number; summary: string | null; rejections: { reason: string; task?: string }[];
    result: { new_analyzed?: number } | null; started_at: string; finished_at: string | null }[];
  tasks: RunTask[];
  findings: Record<string, number>;
}

export interface RunEvent {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type Rating = "up" | "down" | "skip";

export interface VideoRef {
  canonical_id: string;
  platform: string;
  url: string;
  creator: string | null;
  caption: string | null;
  hashtags: string[];
  sound: string | null;
  metrics: { views?: number | null; likes?: number | null; comments?: number | null; shares?: number | null };
  posted_at: string | null;
  duration_s: number | null;
}

export interface TrendCard {
  id: string;
  rank: number;
  label: string | null;
  overall: number | null;
  member_count: number;
  best: VideoRef;
  why: string | null;
  scores: { fit: number | null; feasibility: number | null; momentum: number | null; freshness: number | null;
    overall: number | null };
  fit_breakdown: Record<string, number> | null;
  justification: string | null;
  adaptation_idea: string | null;
  feasibility_notes: string | null;
  niche_guess: string | null;
  disagreement: boolean;
  cross_check: { fit: number; provider: string; justification: string } | null;
  analyst: { provider: string | null; model: string | null };
  motion_window: { start_s: number; end_s: number } | null;
  pose: Record<string, number>;
  camera_motion: number | null;
  contact_sheet_url: string | null;
  members: { url: string; canonical_id: string }[];
  feedback: { rating: Rating; note: string | null } | null;
  has_brief: boolean;
}

export interface TrendsResponse {
  run_id: string;
  state: RunState;
  cards: TrendCard[];
  filtered: { video: VideoRef; status: string; reason: string | null; why: string | null;
    contact_sheet_url: string | null }[];
  run_feedback: { satisfaction: number; note: string | null } | null;
}

export interface BriefBody {
  title: string;
  concept: string;
  record_yourself: string;
  character_orientation: "video" | "image";
  kling_prompt: string;
  framing: string;
  motion_window: { start_s: number; end_s: number };
  shots: { seconds: number; description: string }[];
  risks: string[];
}

export interface Brief {
  cluster_id: string;
  run_id?: string;
  body: BriefBody;
  body_md: string;
  provider: string | null;
  model: string | null;
  created_at: string;
}

export interface Weights { fit: number; feasibility: number; momentum: number; freshness: number }

export interface ProviderStatus {
  provider: string;
  connected: boolean;
  detail: string;
  account: string | null;
  status: string;
  cooling_until: number | null;
  used_percent: number | null;
}
