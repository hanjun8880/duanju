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
  it("tier plan mirrors shot_orchestrator", () => {
    expect(tierPlan("L1_draft", 20)).toEqual({ model_key: "seedance_2_5_draft", resolution: "480p", seconds: 20 });
    expect(tierPlan("L2_master", 20)).toEqual({ model_key: "seedance_2_5_spicy", resolution: "1080p", seconds: 20 });
    expect(tierPlan("L0_animatic", 20)).toEqual({ model_key: "local_animatic", resolution: "720p", seconds: 20 });
  });
});
