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
export type Tier = "L0_animatic" | "L1_draft" | "L2_master" | "L3_post";

export interface BibleCharacter { name: string; role_tag: string; visual_prompt: string; personality: string }
export interface SeriesBible { title?: string; logline?: string; characters?: BibleCharacter[]; props?: { name: string }[]; scenes?: { name: string }[] }
export interface Project { id: string; title: string; category: string; content_mode: ContentMode; aspect_ratio: string; status: string; has_source_text: boolean; series_bible_json: SeriesBible; created_at: string }
export interface Episode { episode_number: number; title: string; synopsis: string; source_segments: number[]; script_text: string | null; status: string }
export interface Output { id: string; kind: "episode" | "series"; url: string; meta: { episode_number?: number | null; shots_used?: number; shots_total?: number; episodes?: number[] }; created_at: string }
export interface Model { model_key: string; display_name: string; task_type: string; is_nsfw: boolean; is_active: boolean; credits_per_unit: string; max_duration: number; max_refs: number; fallback_model_key: string | null; resolution_multipliers: Record<string, number>; health_score: string }
export interface BoundAsset { id: string; name: string; type: "character" | "prop" | "scene"; range: [number, number] }
export interface Shot { id: string; episode_number: number; shot_order: number; template_id: string | null; render_tier: Tier; duration: number; resolution: string; shot_prompt: string; dialogue_transcript: string | null; first_frame_url: string | null; last_frame_url: string | null; refs: { images?: string[]; assets?: BoundAsset[] }; status: string; preview_url: string | null }
export interface LookdevAsset { id: string; asset_type: "character" | "prop" | "scene"; name: string; role_tag: string | null; visual_prompt: string; ref_urls: string[] }
export interface Template { id: string; category: string; relation: string | null; pose: string; scene: string; default_prompt: string; recommended_duration: number; is_nsfw: boolean }
export interface Job { id: string; shot_id: string | null; project_id: string; task_type: string; status: string; model_key: string; credit_estimate: string; actual_credits: string | null; error_message: string | null; created_at: string }
export interface LedgerRow { id: string; job_id: string | null; delta: string; balance: string; reason: string; created_at: string }
export interface Me { credits: string; daily_limit_credits: string; age_verified_18plus: boolean }
