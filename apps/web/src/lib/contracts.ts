import { z } from "zod";

// Mirrors apps/server/src/domains/gateway/schemas.py; contracts.test.ts diffs it against openapi.json.
export const MultimodalRefs = z.object({
  image_urls: z.array(z.string()).max(30).optional(),
  video_urls: z.array(z.string()).max(10).optional(),
  audio_urls: z.array(z.string()).max(10).optional(),
});

export const TaskType = z.enum(["image.t2i", "image.i2i", "video.i2v", "video.t2v", "video.edit", "video.extend", "face.swap", "post.upscale"]);
export const Resolution = z.enum(["480p", "720p", "1080p"]);

export const JobCreateRequest = z.object({
  project_id: z.string().uuid(),
  shot_id: z.string().uuid().nullable().optional(),
  task_type: TaskType,
  model_key: z.string(),
  prompt: z.string().min(1),
  negative_prompt: z.string().nullable().optional(),
  duration: z.number().int().optional(),
  resolution: Resolution.optional(),
  aspect_ratio: z.enum(["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"]).optional(),
  first_frame_url: z.string().nullable().optional(),
  last_frame_url: z.string().nullable().optional(),
  return_last_frame: z.boolean().optional(),
  generate_audio: z.boolean().optional(),
  refs: MultimodalRefs.optional(),
  callback_url: z.string().nullable().optional(),
  edit_source_seconds: z.number().int().min(1).max(600).nullable().optional(),
  likeness_consent: z.boolean().optional(),
});
export type JobCreateRequest = z.infer<typeof JobCreateRequest>;

export type ContentMode = "standard_sfw" | "uncensored_nsfw";
export const TierEnum = z.enum(["L0_animatic", "L1_draft", "L2_master", "L3_post"]);
export type Tier = z.infer<typeof TierEnum>;

// POST /shots/{id}/render and /projects/{id}/episodes/{n}/render (storyboard/api.py RenderRequest)
export const RenderRequest = z.object({
  tier: TierEnum,
  chain: z.boolean().optional(),
  final: z.boolean().optional(), // L2 only: 1080p final instead of the 720p per-kind route
  force: z.boolean().optional(), // re-roll past the content-hash cache
});
export type RenderRequest = z.infer<typeof RenderRequest>;

export interface BibleCharacter { name: string; role_tag: string; visual_prompt: string; personality: string }
export interface SeriesBible { title?: string; logline?: string; characters?: BibleCharacter[]; props?: { name: string }[]; scenes?: { name: string }[] }
export interface Project { id: string; title: string; category: string; content_mode: ContentMode; aspect_ratio: string; status: string; has_source_text: boolean; series_bible_json: SeriesBible; created_at: string }
export interface Episode { episode_number: number; title: string; synopsis: string; source_segments: number[]; script_text: string | null; status: string }
export interface Output { id: string; kind: "episode" | "series"; url: string; meta: { episode_number?: number | null; shots_used?: number; shots_total?: number; episodes?: number[] }; created_at: string }
export interface Model { model_key: string; display_name: string; task_type: string; provider: string; is_nsfw: boolean; is_active: boolean; credits_per_unit: string; max_duration: number; max_refs: number; fallback_model_key: string | null; resolution_multipliers: Record<string, number>; health_score: string; usd_per_unit: string | null; usd_resolution_factors: Record<string, number> }
export interface ProviderRow { provider: string; kinds: string[]; key_envs: string[]; key_configured: boolean }
export interface BoundAsset { id: string; name: string; type: "character" | "prop" | "scene"; range: [number, number] }
export type ShotKind = "dialogue" | "action" | "establishing" | "insert";
export type KeyframeStatus = "none" | "pending" | "ready" | "approved" | "rejected";
export interface Take { job_id: string; render_tier: string | null; model_key: string; final: boolean; url: string; created_at: string }
export interface Shot {
  id: string; episode_number: number; shot_order: number; template_id: string | null; render_tier: Tier; duration: number; resolution: string;
  shot_prompt: string; dialogue_transcript: string | null; first_frame_url: string | null; last_frame_url: string | null;
  refs: { images?: string[]; assets?: BoundAsset[]; asset_names?: string[] }; status: string; preview_url: string | null;
  shot_kind: ShotKind; continuous_from_prev: boolean; keyframe_url: string | null; keyframe_status: KeyframeStatus;
  selected_job_id: string | null; takes: Take[];
}
export interface LookdevAsset { id: string; asset_type: "character" | "prop" | "scene"; name: string; role_tag: string | null; visual_prompt: string; ref_urls: string[]; locked_ref_urls: string[] }
export interface Template { id: string; category: string; relation: string | null; pose: string; scene: string; default_prompt: string; recommended_duration: number; is_nsfw: boolean }
export interface Job { id: string; shot_id: string | null; project_id: string; task_type: string; status: string; model_key: string; credit_estimate: string; actual_credits: string | null; error_message: string | null; created_at: string; usd: string; stage: string | null; cached_from: string | null }
export interface LedgerRow { id: string; job_id: string | null; delta: string; balance: string; reason: string; created_at: string }
export interface Me { credits: string; daily_limit_credits: string; age_verified_18plus: boolean }

// ---- shapes added by the team branches (unified at integration; all are dict responses, not in openapi.json) ----

// look-dev
export type LookdevKind = "portrait" | "turnaround" | "expressions" | "costume";
export type LookdevJob = Job & { cached: boolean; kind: LookdevKind };
export interface LookdevEstimate { kind: LookdevKind; images: number; usd: number; cached: boolean }

// keyframes, routing, money (GET /projects/{id}/episodes/{n}/keyframes|estimate, /projects/{id}/costs, /admin/stages)
export interface KeyframeRow {
  shot_id: string; episode_number: number; shot_order: number; shot_kind: string; shot_prompt: string;
  keyframe_url: string | null; keyframe_status: KeyframeStatus;
}
export interface EpisodeEstimate {
  episode_number: number; tier: string; final: boolean; usd: string; total_usd: string; credits: string;
  budget_usd: string | null; keyframes: { count: number; model_key: string; usd: string };
  shots: { shot_id: string; shot_order: number; stage: string | null; model_key: string; resolution: string; seconds: number; usd: string | null }[];
}
export type CostPhase = "script" | "remix" | "lookdev" | "keyframe" | "video" | "voice" | "post" | (string & {});
export interface StageCost { stage: string; phase: CostPhase; jobs: number; usd: string; in_flight_usd: string; cached: number; failed: number }
export interface ProjectCosts {
  spent_usd: string; in_flight_usd: string; total_usd: string; budget_usd: string | null;
  by_stage: StageCost[]; by_phase: { phase: CostPhase; usd: string }[]; by_episode: { episode_number: number; usd: string }[];
}
export interface StageRow {
  stage: string; kind: string; model: string; provider: string; resolution: string | null; note: string;
  overridden: boolean; default: { model: string; provider: string; resolution: string | null };
}

// script room (GET /projects/{id}/plan/progress, /projects/{id}/llm-usage)
export type PlanProgress = {
  stage: "idle" | "summarize" | "plan" | "outline" | "done" | "error";
  done?: number; total?: number; episodes?: number; error?: string; cached_segments?: number;
};
export type LlmUsage = {
  calls: number; total_usd: number;
  by_stage: Record<string, { calls: number; usd: number; input_tokens: number; output_tokens: number; unpriced_calls: number }>;
};

export interface QualityTierOption {
  model: string; provider: string; price_note: string;
  effective_model: string; effective_provider: string;
}
export type StageTiers = Record<string, Record<string, QualityTierOption>>;
export interface ProjectQualityTiers { tiers: Record<string, string> }

// remix studio (/remix/*)
export type RemixMode = "recast" | "restyle" | "replot" | "sequel" | "spinoff" | "condense";
export interface RemixSpec {
  modes: RemixMode[]; recast?: Record<string, string>; style?: string; plot_direction?: string;
  spinoff_focus?: string; condense_ratio?: number; episode_count?: number;
}
export interface RemixSource {
  episode_number: number; status: string; progress: number; sha256: string | null; cache_hit: boolean;
  shots: number | null; kept: number | null; error: string | null; video_path: string;
}
export interface RemixEstimate {
  model: string; price_verified: boolean;
  episodes: { episode_number: number; status: string; shots?: number; images: number | null; images_without_dedupe?: number; usd: number | null }[];
  total: { images: number; batches: number; usd: number | null };
}
export interface RemixProject { id: string; title: string; category: string; status: string; episodes?: number }
