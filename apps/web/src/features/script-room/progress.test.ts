import { describe, expect, it } from "vitest";
import { progressText, usageText } from "./progress";

describe("plan progress", () => {
  it("describes each stage", () => {
    expect(progressText({ stage: "idle" })).toBeNull();
    expect(progressText({ stage: "summarize", done: 8, total: 20 })).toBe("逐段读原文：8 / 20 段");
    expect(progressText({ stage: "outline", done: 20, total: 25 })).toBe("写分集大纲：20 / 25 集");
    expect(progressText({ stage: "done", episodes: 25 })).toBe("规划完成，共 25 集");
    expect(progressText({ stage: "error", error: "truncated" })).toContain("truncated");
  });
  it("sums spend by stage", () => {
    expect(usageText({ calls: 0, total_usd: 0, by_stage: {} })).toBeNull();
    const t = usageText({ calls: 6, total_usd: 0.4567, by_stage: {
      summarize: { calls: 5, usd: 0.0567, input_tokens: 1, output_tokens: 1, unpriced_calls: 0 },
      plan: { calls: 1, usd: 0.4, input_tokens: 1, output_tokens: 1, unpriced_calls: 1 } } });
    expect(t).toBe("编剧 AI 花费约 $0.46（6 次调用：摘要 $0.06，规划 $0.40），另有 1 次未定价");
  });
});
