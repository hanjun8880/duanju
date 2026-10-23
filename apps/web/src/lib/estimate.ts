import type { Model, ShotKind, StageRow, Tier } from "./contracts";

type Priced = Pick<Model, "credits_per_unit" | "resolution_multipliers">;

// Same formula as credit_ledger.estimate_credits (authoritative value comes back from POST /jobs).
export function estimateCredits(model: Priced, seconds: number, resolution: string): number {
  const mult = model.resolution_multipliers[resolution] ?? 1;
  return Number(model.credits_per_unit) * seconds * mult;
}

/** Default video routing, mirrors gateway/routing.py STAGES (admin overrides come from GET /admin/stages). */
export const VIDEO_ROUTES: Record<string, { model: string; resolution: string }> = {
  video_draft: { model: "seedance_2_5_draft", resolution: "480p" },
  video_dialogue: { model: "seedance_2_5_spicy", resolution: "720p" },
  video_action: { model: "wan3_spicy", resolution: "720p" },
  video_establishing: { model: "wan3_spicy", resolution: "720p" },
  video_insert: { model: "wan3_spicy", resolution: "720p" },
  video_final: { model: "seedance_2_5_spicy", resolution: "1080p" },
};

/** routing.video_stage_for: draft -> video_draft; final -> video_final; else video_<shot_kind> (dialogue default). */
export function videoStageFor(kind: ShotKind | undefined, opts: { final?: boolean; draft?: boolean } = {}): string {
  if (opts.draft) return "video_draft";
  if (opts.final) return "video_final";
  const s = `video_${kind ?? "dialogue"}`;
  return s in VIDEO_ROUTES ? s : "video_dialogue";
}

export interface PlanOpts { shotKind?: ShotKind; final?: boolean; stages?: Pick<StageRow, "stage" | "model" | "resolution">[] }

// Mirrors shot_orchestrator.shot_request: L1 = draft route (480p); L2 = route by shot kind (720p),
// 1080p only on an explicit final render. `stages` (GET /admin/stages) applies the admin's overrides.
export function tierPlan(tier: Tier, duration: number, opts: PlanOpts = {}): { model_key: string; resolution: string; seconds: number } {
  const seconds = Math.min(Math.max(duration, 4), 30);
  if (tier === "L1_draft" || tier === "L2_master") {
    const stage = videoStageFor(opts.shotKind, { draft: tier === "L1_draft", final: opts.final });
    const over = opts.stages?.find((s) => s.stage === stage);
    const base = VIDEO_ROUTES[stage];
    return { model_key: over?.model ?? base.model, resolution: over?.resolution ?? base.resolution, seconds };
  }
  if (tier === "L3_post") return { model_key: "realesrgan_x4plus", resolution: "1080p", seconds: duration };
  return { model_key: "local_animatic", resolution: "720p", seconds: duration };
}
