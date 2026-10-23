import { describe, expect, it } from "vitest";
import { budgetUse, canReview, needsKeyframe, usd } from "./money";

describe("money helpers", () => {
  it("formats dollars, keeping sub-cent amounts visible", () => {
    expect(usd("2.3100")).toBe("$2.31");
    expect(usd("0.0030")).toBe("$0.003");
    expect(usd(0)).toBe("$0.00");
    expect(usd(null)).toBe("—");
    expect(usd("abc")).toBe("—");
  });
  it("budget share is null without a budget", () => {
    expect(budgetUse("5", "10")).toBe(0.5);
    expect(budgetUse("5", null)).toBeNull();
    expect(budgetUse("5", "0")).toBeNull();
  });
  it("review and regenerate rules match the backend", () => {
    expect(canReview("ready")).toBe(true);
    expect(canReview("pending")).toBe(false);
    expect(canReview("none")).toBe(false);
    expect(needsKeyframe("rejected")).toBe(true);
    expect(needsKeyframe("approved")).toBe(false);
  });
});
