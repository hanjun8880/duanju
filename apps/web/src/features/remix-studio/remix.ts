import type { RemixMode, RemixSource, RemixSpec } from "@/lib/contracts";

export const MODES: { id: RemixMode; label: string; hint: string }[] = [
  { id: "recast", label: "换人物", hint: "每行「原角色=新设定」；留空则全部人物重新设计" },
  { id: "restyle", label: "换风格", hint: "目标视觉/题材风格，如：民国、赛博朋克" },
  { id: "replot", label: "改剧情", hint: "剧情改动方向" },
  { id: "sequel", label: "续写", hint: "从结尾继续写" },
  { id: "spinoff", label: "番外", hint: "围绕的人物或支线" },
  { id: "condense", label: "精简剪辑", hint: "目标集数占原片比例，0–1" },
];

export const STATUS_LABEL: Record<string, string> = {
  registered: "已登记", queued: "排队", deconstructing: "本地拆解中", deconstructed: "已拆解",
  describing: "识别画面中", described: "已识别", failed: "失败",
};

export interface SpecForm {
  modes: RemixMode[]; recastText?: string; style?: string; plot_direction?: string; spinoff_focus?: string;
  condense_ratio?: number | null; episode_count?: number | null;
}

const txt = (v?: string) => (v && v.trim() ? v.trim() : undefined);

export function buildSpec(f: SpecForm): RemixSpec {
  const recast: Record<string, string> = {};
  for (const line of (f.recastText ?? "").split("\n")) {
    const i = line.indexOf("=");
    if (i > 0 && line.slice(0, i).trim()) recast[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  const spec: RemixSpec = { modes: f.modes };
  if (f.modes.includes("recast") && Object.keys(recast).length) spec.recast = recast;
  const style = txt(f.style), plot = txt(f.plot_direction), focus = txt(f.spinoff_focus);
  if (style) spec.style = style;
  if (plot) spec.plot_direction = plot;
  if (focus) spec.spinoff_focus = focus;
  if (f.condense_ratio != null) spec.condense_ratio = f.condense_ratio;
  if (f.episode_count) spec.episode_count = f.episode_count;
  return spec;
}

/** Same rules as the backend RemixSpec validator, so the button explains itself before a 422. */
export function validateSpec(s: RemixSpec): string | null {
  if (!s.modes.length) return "至少选一种二创方式";
  if (s.modes.includes("restyle") && !s.style) return "换风格需要填写目标风格";
  if (s.modes.includes("replot") && !s.plot_direction) return "改剧情需要填写剧情方向";
  if (s.modes.includes("spinoff") && !s.spinoff_focus) return "番外需要填写围绕的人物或支线";
  if (s.modes.includes("condense") && !(s.condense_ratio != null && s.condense_ratio > 0 && s.condense_ratio < 1))
    return "精简剪辑需要 0 到 1 之间的比例";
  if (s.modes.includes("sequel") && s.modes.includes("condense")) return "续写和精简剪辑不能同时选";
  return null;
}

export const isWorking = (xs: RemixSource[]) => xs.some((x) => ["queued", "deconstructing", "describing"].includes(x.status));
export const allReady = (xs: RemixSource[]) => xs.length > 0 && xs.every((x) => x.status === "deconstructed" || x.status === "described");
