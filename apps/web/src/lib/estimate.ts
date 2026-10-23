import type { Model, Tier } from "./contracts";

type Priced = Pick<Model, "credits_per_unit" | "resolution_multipliers">;

// Same formula as credit_ledger.estimate_credits (authoritative value comes back from POST /jobs).
export function estimateCredits(model: Priced, seconds: number, resolution: string): number {
  const mult = model.resolution_multipliers[resolution] ?? 1;
  return Number(model.credits_per_unit) * seconds * mult;
}

// Mirrors shot_orchestrator tier rules.
export function tierPlan(tier: Tier, duration: number): { model_key: string; resolution: string; seconds: number } {
  if (tier === "L1_draft") return { model_key: "seedance_2_5_draft", resolution: "480p", seconds: Math.min(Math.max(duration, 4), 30) };
  if (tier === "L2_master") return { model_key: "seedance_2_5_spicy", resolution: "1080p", seconds: Math.min(Math.max(duration, 4), 30) };
  if (tier === "L3_post") return { model_key: "realesrgan_x4plus", resolution: "1080p", seconds: duration };
  return { model_key: "local_animatic", resolution: "720p", seconds: duration };
}
