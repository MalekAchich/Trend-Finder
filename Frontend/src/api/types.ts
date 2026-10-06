export type Platform = "tiktok" | "instagram" | "youtube";

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
