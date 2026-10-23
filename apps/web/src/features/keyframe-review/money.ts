export type KeyframeStatus = "none" | "pending" | "ready" | "approved" | "rejected";

export interface KeyframeRow {
  shot_id: string; episode_number: number; shot_order: number; shot_kind: string; shot_prompt: string;
  keyframe_url: string | null; keyframe_status: KeyframeStatus;
}

export interface EpisodeEstimate {
  episode_number: number; tier: string; final: boolean; usd: string; total_usd: string; credits: string;
  budget_usd: string | null; keyframes: { count: number; model_key: string; usd: string };
  shots: { shot_id: string; shot_order: number; stage: string | null; model_key: string; resolution: string; seconds: number; usd: string | null }[];
}

export interface StageCost { stage: string; jobs: number; usd: string; in_flight_usd: string; cached: number; failed: number }
export interface ProjectCosts {
  spent_usd: string; in_flight_usd: string; total_usd: string; budget_usd: string | null;
  by_stage: StageCost[]; by_episode: { episode_number: number; usd: string }[];
}

export const STATUS_LABEL: Record<KeyframeStatus, string> = {
  none: "未生成", pending: "生成中", ready: "待审核", approved: "已通过", rejected: "已驳回",
};

export const STAGE_LABEL: Record<string, string> = {
  summarize: "小说摘要", plan: "整剧规划", script: "分集剧本", storyboard: "分镜表", reformat: "剧本整理",
  remix_vision: "二创识别", remix_rewrite: "二创改写", lookdev_image: "定妆图", keyframe_image: "关键帧",
  video_draft: "视频草稿", video_dialogue: "对白镜头", video_action: "动作镜头", video_establishing: "空镜",
  video_insert: "插入镜头", video_final: "最终版", tts: "配音", post: "本地后期", image: "图片", video: "视频",
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
