"use client";
import { useState } from "react";
import { keyframeApi, useApi, useBusySet } from "@/lib/api";
import { useWorkbench } from "@/lib/store";
import type { EpisodeEstimate, KeyframeRow } from "@/lib/contracts";
import { STATUS_LABEL, budgetUse, canReview, needsKeyframe, usd } from "./money";

type Tier = "L1_draft" | "L2_master";

/** 关键帧审核：每镜一张静帧（锁定定妆参考），通过后再花钱出视频。 */
export function KeyframeReview({ projectId, onSpent }: { projectId: string; onSpent?: () => void }) {
  const { episode } = useWorkbench();
  const rows = useApi<KeyframeRow[]>(`/projects/${projectId}/episodes/${episode}/keyframes`, 4000);
  const [tier, setTier] = useState<Tier>("L2_master");
  const [final, setFinal] = useState(false);
  const est = useApi<EpisodeEstimate>(`/projects/${projectId}/episodes/${episode}/estimate?tier=${tier}&final=${final}`);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const { busy, run } = useBusySet<string>();

  const act = (key: string, fn: () => Promise<unknown>, ok?: string) => run(key, async () => {
    try { await fn(); setMsg(ok ? { ok: true, text: ok } : null); onSpent?.(); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    rows.reload(); est.reload();
  });

  const list = rows.data ?? [];
  const missing = list.filter((r) => needsKeyframe(r.keyframe_status)).length;
  const approved = list.filter((r) => r.keyframe_status === "approved").length;
  const share = est.data ? budgetUse(est.data.total_usd, est.data.budget_usd) : null;

  return (
    <section aria-labelledby="kf-title" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 id="kf-title" className="text-lg font-semibold">第 {episode} 集 · 关键帧审核
          <span className="ml-3 text-sm font-normal text-ink-soft">已通过 {approved}／{list.length}</span></h3>
        <button className="btn" disabled={missing === 0 || busy.has("all")}
          onClick={() => act("all", async () => {
            const r = await keyframeApi.generateEpisode(projectId, episode);
            if (r.error) throw new Error(`已排队 ${r.jobs.length} 张，其余未生成：${r.error}`);
          }, `已为 ${missing} 个镜头排队生成关键帧`)}>
          {busy.has("all") ? "排队中…" : `生成缺失关键帧（${missing}）`}
        </button>
      </div>

      {est.data && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded border border-celadon px-4 py-3 text-sm">
          <label className="flex items-center gap-2">档位
            <select className="field py-0.5" value={tier} onChange={(e) => setTier(e.target.value as Tier)}>
              <option value="L1_draft">草稿 480p</option><option value="L2_master">正片（按镜头类型）</option>
            </select></label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={final} disabled={tier !== "L2_master"}
            onChange={(e) => setFinal(e.target.checked)} />最终版 1080p</label>
          <span>视频约 <b>{usd(est.data.usd)}</b></span>
          <span>关键帧 {est.data.keyframes.count} 张约 {usd(est.data.keyframes.usd)}</span>
          <span>合计约 <b>{usd(est.data.total_usd)}</b></span>
          {share !== null && <span className={share > 1 ? "text-seal" : "text-ink-soft"}>本集预算 {usd(est.data.budget_usd)}（{Math.round(share * 100)}%）</span>}
          <span className="w-full text-xs text-ink-soft">参考价（第三方 2026-10，未经官方核实），按当前模型分工估算。</span>
        </div>
      )}

      {msg && <p role={msg.ok ? "status" : "alert"} className={msg.ok ? "text-dawn" : "text-seal"}>{msg.text}</p>}
      {rows.loading && <p className="text-ink-soft">加载中…</p>}
      {!rows.loading && list.length === 0 && <p className="text-ink-soft">这一集还没有镜头，先在分镜里导入。</p>}

      <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {list.map((r) => (
          <li key={r.shot_id} className="flex flex-col overflow-hidden rounded border border-celadon">
            <div className="relative aspect-[9/16] bg-celadon/30">
              {r.keyframe_url
                ? <img src={r.keyframe_url} alt={`第 ${r.shot_order} 镜关键帧`} className="h-full w-full object-cover" />
                : <span className="absolute inset-0 flex items-center justify-center text-sm text-ink-soft">{STATUS_LABEL[r.keyframe_status]}</span>}
              <span className={`absolute left-2 top-2 rounded bg-paper/90 px-1.5 text-xs ${r.keyframe_status === "rejected" ? "text-seal" : r.keyframe_status === "approved" ? "text-dawn" : ""}`}>
                {r.shot_order} · {STATUS_LABEL[r.keyframe_status]}</span>
            </div>
            <p className="line-clamp-2 px-2 pt-2 text-xs text-ink-soft" title={r.shot_prompt}>{r.shot_prompt}</p>
            <div className="mt-auto flex flex-wrap gap-1 p-2">
              {canReview(r.keyframe_status) && r.keyframe_status !== "approved" &&
                <button className="btn py-0.5" disabled={busy.has(r.shot_id)} onClick={() => act(r.shot_id, () => keyframeApi.approve(r.shot_id))}>通过</button>}
              {canReview(r.keyframe_status) && r.keyframe_status !== "rejected" &&
                <button className="btn btn-quiet py-0.5" disabled={busy.has(r.shot_id)} onClick={() => act(r.shot_id, () => keyframeApi.reject(r.shot_id))}>驳回</button>}
              {r.keyframe_status !== "pending" &&
                <button className="btn btn-quiet py-0.5" disabled={busy.has(r.shot_id)}
                  onClick={() => act(r.shot_id, () => keyframeApi.generate(r.shot_id, r.keyframe_status !== "none"))}>
                  {r.keyframe_status === "none" ? "生成" : "重抽"}</button>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
