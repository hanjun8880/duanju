/** Shapes of GET /projects/{id}/plan/progress and /llm-usage (local until openapi/contracts are regenerated). */
export type PlanProgress = {
  stage: "idle" | "summarize" | "plan" | "outline" | "done" | "error";
  done?: number; total?: number; episodes?: number; error?: string; cached_segments?: number;
};
export type LlmUsage = {
  calls: number; total_usd: number;
  by_stage: Record<string, { calls: number; usd: number; input_tokens: number; output_tokens: number; unpriced_calls: number }>;
};

const STAGE_LABEL: Record<string, string> = { summarize: "摘要", plan: "规划", script: "剧本", storyboard: "分镜", reformat: "整理", remix_vision: "原片识别" };

export function progressText(p: PlanProgress | null | undefined): string | null {
  if (!p) return null;
  const n = `${p.done ?? 0} / ${p.total ?? 0}`;
  switch (p.stage) {
    case "summarize": return `逐段读原文：${n} 段`;
    case "plan": return "锁定人物、道具、场景设定…";
    case "outline": return `写分集大纲：${n} 集`;
    case "done": return `规划完成，共 ${p.episodes ?? p.total ?? 0} 集`;
    case "error": return `规划失败：${p.error ?? "未知错误"}（已读过的段落会保留，重试不再重复花费）`;
    default: return null;
  }
}

export function usageText(u: LlmUsage | null | undefined): string | null {
  if (!u || !u.calls) return null;
  const parts = Object.entries(u.by_stage).map(([k, v]) => `${STAGE_LABEL[k] ?? k} $${v.usd.toFixed(2)}`);
  const unpriced = Object.values(u.by_stage).reduce((a, v) => a + v.unpriced_calls, 0);
  return `编剧 AI 花费约 $${u.total_usd.toFixed(2)}（${u.calls} 次调用：${parts.join("，")}）` + (unpriced ? `，另有 ${unpriced} 次未定价` : "");
}
