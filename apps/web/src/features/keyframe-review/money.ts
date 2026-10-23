import type { KeyframeStatus } from "@/lib/contracts";

export const STATUS_LABEL: Record<KeyframeStatus, string> = {
  none: "未生成", pending: "生成中", ready: "待审核", approved: "已通过", rejected: "已驳回",
};

export const STAGE_LABEL: Record<string, string> = {
  summarize: "小说摘要", plan: "整剧规划", script: "分集剧本", storyboard: "分镜表", reformat: "剧本整理",
  remix_vision: "二创识别", remix_rewrite: "二创改写", lookdev_image: "定妆图", keyframe_image: "关键帧",
  video_draft: "视频草稿", video_dialogue: "对白镜头", video_action: "动作镜头", video_establishing: "空镜",
  video_insert: "插入镜头", video_final: "最终版", tts: "配音", post: "本地后期", image: "图片", video: "视频",
};

/** Coarse phases of GET /projects/{id}/costs by_phase (LLM calls land in 编剧 / 二创). */
export const PHASE_LABEL: Record<string, string> = {
  script: "编剧", remix: "二创", lookdev: "定妆", keyframe: "关键帧", video: "视频", voice: "配音", post: "后期",
};

/** "$1.23"; amounts under a cent keep 3 decimals so a $0.003 image is not shown as $0.00. */
export function usd(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n !== 0 && Math.abs(n) < 0.01 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`;
}

/** Share of the budget used, 0..1+ (null when there is no budget). */
export function budgetUse(total: string | number, budget: string | null | undefined): number | null {
  if (budget === null || budget === undefined || Number(budget) <= 0) return null;
  return Number(total) / Number(budget);
}

export const canReview = (s: KeyframeStatus) => s === "ready" || s === "approved" || s === "rejected";
export const needsKeyframe = (s: KeyframeStatus) => s === "none" || s === "rejected";
