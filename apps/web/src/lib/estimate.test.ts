import { describe, expect, it } from "vitest";
import { estimateCredits, tierPlan } from "./estimate";

const seedance = { model_key: "seedance_2_5_spicy", credits_per_unit: "1.0000", resolution_multipliers: { "480p": 1, "720p": 1.8, "1080p": 3.2 } };

describe("credit burn estimator matches the backend ledger formula", () => {
  it("unit price x seconds x resolution multiplier", () => {
    expect(estimateCredits(seedance, 20, "1080p")).toBeCloseTo(64);
    expect(estimateCredits(seedance, 10, "720p")).toBeCloseTo(18);
  });
  it("unknown resolution falls back to x1", () => {
    expect(estimateCredits({ ...seedance, resolution_multipliers: {} }, 5, "1080p")).toBe(5);
  });
  // Integration: L2 follows gateway/routing (720p per shot kind, 1080p only when final). The old assertion
  // (L2 = seedance 1080p) described the pre-routing orchestrator and over-estimated every master.
  it("tier plan mirrors shot_orchestrator routing", () => {
    expect(tierPlan("L1_draft", 20)).toEqual({ model_key: "seedance_2_5_draft", resolution: "480p", seconds: 20 });
    expect(tierPlan("L2_master", 20)).toEqual({ model_key: "seedance_2_5_spicy", resolution: "720p", seconds: 20 });
    expect(tierPlan("L2_master", 6, { shotKind: "action" })).toEqual({ model_key: "wan3_spicy", resolution: "720p", seconds: 6 });
    expect(tierPlan("L2_master", 6, { shotKind: "establishing" }).model_key).toBe("wan3_spicy");
    expect(tierPlan("L2_master", 6, { shotKind: "action", final: true })).toEqual({ model_key: "seedance_2_5_spicy", resolution: "1080p", seconds: 6 });
    expect(tierPlan("L2_master", 2).seconds).toBe(4);
    expect(tierPlan("L0_animatic", 20)).toEqual({ model_key: "local_animatic", resolution: "720p", seconds: 20 });
  });
  it("admin stage overrides win", () => {
    const stages = [{ stage: "video_action", model: "minimax_h3", resolution: "720p" }];
    expect(tierPlan("L2_master", 6, { shotKind: "action", stages }).model_key).toBe("minimax_h3");
    expect(tierPlan("L2_master", 6, { shotKind: "dialogue", stages }).model_key).toBe("seedance_2_5_spicy");
  });
});
