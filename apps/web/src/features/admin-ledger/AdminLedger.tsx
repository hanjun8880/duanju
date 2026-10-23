"use client";
import { useEffect, useRef, useState } from "react";
import { api, putBudget, putStage, useApi } from "@/lib/api";
import type { Job, LedgerRow, Model } from "@/lib/contracts";
import { useWorkbench } from "@/lib/store";
import { type ProjectCosts, STAGE_LABEL, budgetUse, usd } from "@/features/keyframe-review/money";

type JobX = Job & { usd?: string; stage?: string | null; cached_from?: string | null };
type ModelX = Model & { usd_per_unit?: string | null };
interface StageRow {
  stage: string; kind: string; model: string; provider: string; resolution: string | null; note: string;
  overridden: boolean; default: { model: string; provider: string; resolution: string | null };
}
type View = "ledger" | "stages" | "costs";
const VIEWS: { id: View; label: string }[] = [{ id: "costs", label: "花费" }, { id: "stages", label: "模型分工" }, { id: "ledger", label: "账本与模型" }];

const REASON: Record<string, string> = {
  pre_deduct: "预扣", settle_refund: "结算退回", settle_extra_charge: "结算补扣", provider_5xx_refund: "供应商故障退回",
  provider_4xx_refund: "参数被拒退回", timeout_refund: "超时退回", policy_reject_refund: "内容策略退回",
  cancel_refund: "取消退回", manual_topup: "充值",
};
const STATUS: Record<string, string> = {
  queued: "排队", processing: "渲染中", success: "完成", failed: "失败", cancelled: "已取消", dead_letter: "死信",
};

export function AdminLedger({ onClose }: { onClose: () => void }) {
  const models = useApi<ModelX[]>("/admin/models");
  const jobs = useApi<JobX[]>("/admin/jobs?limit=30");
  const ledger = useApi<LedgerRow[]>("/ledger?limit=30");
  const stages = useApi<StageRow[]>("/admin/stages");
  const { projectId } = useWorkbench();
  const costs = useApi<ProjectCosts>(projectId ? `/projects/${projectId}/costs` : null);
  const [view, setView] = useState<View>("costs");
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { setErr((e as Error).message); }
    models.reload(); jobs.reload(); ledger.reload(); stages.reload(); costs.reload();
  };
  const share = costs.data ? budgetUse(costs.data.total_usd, costs.data.budget_usd) : null;

  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-ink/30" onClick={onClose}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="ledger-title"
        className="h-full w-full max-w-2xl overflow-y-auto bg-paper px-6 py-6 shadow-[-12px_0_32px_rgb(34_48_47/0.18)]" onClick={(e) => e.stopPropagation()}>
        <div className="mb-6 flex items-baseline justify-between">
          <h2 id="ledger-title" className="text-2xl font-semibold">账本与模型</h2>
          <button className="btn btn-quiet" onClick={onClose}>关闭</button>
        </div>
        {err && <p className="mb-4 text-seal" role="alert">{err}</p>}
        <div role="tablist" className="mb-6 flex gap-2">
          {VIEWS.map((v) => (
            <button key={v.id} role="tab" aria-selected={view === v.id} className={`btn ${view === v.id ? "" : "btn-quiet"} py-0.5`}
              onClick={() => setView(v.id)}>{v.label}</button>
          ))}
        </div>

        {view === "costs" && (!projectId ? <p className="text-ink-soft">先选一个项目。</p> : costs.data && (
          <div className="mb-8 space-y-4 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
              <span className="text-2xl font-semibold">{usd(costs.data.total_usd)}</span>
              <span className="text-ink-soft">已结算 {usd(costs.data.spent_usd)} · 进行中 {usd(costs.data.in_flight_usd)}</span>
              <label className="flex items-center gap-2">项目预算 $
                <input aria-label="项目预算（美元）" className="field w-24 py-0.5" defaultValue={costs.data.budget_usd ?? ""} placeholder="不限"
                  onBlur={(e) => e.target.value !== (costs.data?.budget_usd ?? "") && act(() => putBudget(projectId, e.target.value.trim() || null))} />
              </label>
              {share !== null && <span className={share > 0.9 ? "text-seal" : "text-ink-soft"}>已用 {Math.round(share * 100)}%</span>}
            </div>
            <table className="w-full">
              <thead><tr className="text-left text-ink-soft"><th className="py-1 font-normal">环节</th><th className="font-normal">任务</th><th className="font-normal">缓存命中</th><th className="text-right font-normal">美元</th></tr></thead>
              <tbody>
                {costs.data.by_stage.length === 0 && <tr><td className="py-2 text-ink-soft" colSpan={4}>还没有花费。</td></tr>}
                {costs.data.by_stage.map((b) => (
                  <tr key={b.stage} className="border-t border-celadon">
                    <td className="py-1.5">{STAGE_LABEL[b.stage] ?? b.stage}</td>
                    <td>{b.jobs}{b.failed ? <span className="ml-1 text-xs text-ink-soft">（失败退回 {b.failed}）</span> : null}</td>
                    <td>{b.cached || "—"}</td>
                    <td className="text-right">{usd(Number(b.usd) + Number(b.in_flight_usd))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {costs.data.by_episode.length > 0 && <p className="text-ink-soft">按集：{costs.data.by_episode.map((e) => `第${e.episode_number}集 ${usd(e.usd)}`).join(" · ")}</p>}
            <p className="text-xs text-ink-soft">美元为参考价（第三方 2026-10，未经官方核实），可在“账本与模型”里改单价。</p>
          </div>
        ))}

        {view === "stages" && (
          <table className="mb-8 w-full text-sm">
            <thead><tr className="text-left text-ink-soft"><th className="py-1 font-normal">环节</th><th className="font-normal">模型</th><th className="font-normal">分辨率</th><th /></tr></thead>
            <tbody>
              {stages.data?.map((st) => {
                const family = st.kind === "image" || st.kind === "video" ? st.kind : null;
                const options = family ? (models.data ?? []).filter((m) => m.task_type.startsWith(family + ".") && m.is_active) : [];
                return (
                  <tr key={st.stage} className="border-t border-celadon align-top">
                    <td className="py-1.5">{STAGE_LABEL[st.stage] ?? st.stage}<div className="text-xs text-ink-soft">{st.note}</div></td>
                    <td>{family
                      ? <select aria-label={`${st.stage} 模型`} className="field py-0.5" value={st.model}
                          onChange={(e) => act(() => putStage({ stage: st.stage, model: e.target.value, resolution: st.resolution }))}>
                          {options.some((m) => m.model_key === st.model) ? null : <option value={st.model}>{st.model}</option>}
                          {options.map((m) => <option key={m.model_key} value={m.model_key}>{m.display_name}</option>)}
                        </select>
                      : <input aria-label={`${st.stage} 模型`} className="field w-44 py-0.5" defaultValue={st.model}
                          onBlur={(e) => e.target.value.trim() && e.target.value !== st.model && act(() => putStage({ stage: st.stage, model: e.target.value.trim() }))} />}
                    </td>
                    <td>{st.kind === "video"
                      ? <select aria-label={`${st.stage} 分辨率`} className="field py-0.5" value={st.resolution ?? "720p"}
                          onChange={(e) => act(() => putStage({ stage: st.stage, model: st.model, resolution: e.target.value }))}>
                          {["480p", "720p", "1080p"].map((r) => <option key={r}>{r}</option>)}
                        </select>
                      : <span className="text-ink-soft">—</span>}</td>
                    <td>{st.overridden && <button className="btn btn-quiet py-0.5" onClick={() => act(() => putStage({ stage: st.stage, model: null }))}>恢复默认</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {view === "ledger" && <>
        <h3 className="mb-2 font-semibold">模型开关与单价</h3>
        <table className="mb-8 w-full text-sm">
          <thead><tr className="text-left text-ink-soft"><th className="py-1 font-normal">模型</th><th className="font-normal">积分／单位</th><th className="font-normal">美元／单位</th><th className="font-normal">降级到</th><th className="font-normal">启用</th></tr></thead>
          <tbody>
            {models.data?.map((m) => (
              <tr key={m.model_key} className="border-t border-celadon">
                <td className="py-1.5">{m.display_name}<div className="text-xs text-ink-soft">{m.model_key}</div></td>
                <td><input aria-label={`${m.model_key} 单价`} className="field w-20 py-0.5" defaultValue={Number(m.credits_per_unit)}
                  onBlur={(e) => e.target.value !== String(Number(m.credits_per_unit)) && act(() => api(`/admin/models/${m.model_key}`, { method: "PATCH", json: { credits_per_unit: e.target.value } }))} /></td>
                <td><input aria-label={`${m.model_key} 美元单价`} className="field w-20 py-0.5" defaultValue={m.usd_per_unit ?? ""} placeholder="未设"
                  onBlur={(e) => e.target.value && e.target.value !== (m.usd_per_unit ?? "") && act(() => api(`/admin/models/${m.model_key}`, { method: "PATCH", json: { usd_per_unit: e.target.value } }))} /></td>
                <td className="text-ink-soft">{m.fallback_model_key ?? "无"}</td>
                <td><input type="checkbox" aria-label={`启用 ${m.model_key}`} checked={m.is_active}
                  onChange={(e) => act(() => api(`/admin/models/${m.model_key}`, { method: "PATCH", json: { is_active: e.target.checked } }))} /></td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3 className="mb-2 font-semibold">任务</h3>
        <ul className="mb-8 divide-y divide-celadon text-sm">
          {jobs.data?.length === 0 && <li className="py-2 text-ink-soft">还没有生成任务。</li>}
          {jobs.data?.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
              <span className={`w-14 ${j.status === "failed" || j.status === "dead_letter" ? "text-seal" : ""}`}>{STATUS[j.status] ?? j.status}</span>
              <span className="min-w-0 flex-1">{j.model_key}<span className="ml-2 text-ink-soft">{j.task_type}</span>
                {j.error_message && <span className="block text-xs text-ink-soft">{j.error_message}</span>}</span>
              <span>{Number(j.actual_credits ?? j.credit_estimate).toFixed(1)}</span>
              <span className="w-16 text-right">{j.cached_from ? <span className="text-dawn">缓存</span> : usd(j.usd)}</span>
              {(j.status === "queued" || j.status === "processing") &&
                <button className="btn btn-quiet py-0.5" onClick={() => act(() => api(`/admin/jobs/${j.id}/refund`, { method: "POST" }))}>取消并退回</button>}
              {(j.status === "failed" || j.status === "cancelled") &&
                <button className="btn btn-quiet py-0.5" onClick={() => act(() => api(`/admin/jobs/${j.id}/retry`, { method: "POST" }))}>重试</button>}
            </li>
          ))}
        </ul>

        <h3 className="mb-2 font-semibold">积分流水</h3>
        <table className="w-full text-sm">
          <tbody>
            {ledger.data?.map((r) => (
              <tr key={r.id} className="border-t border-celadon">
                <td className="py-1.5 text-ink-soft">{new Date(r.created_at).toLocaleString("zh-CN", { hour12: false })}</td>
                <td>{REASON[r.reason] ?? r.reason}</td>
                <td className={`text-right ${Number(r.delta) < 0 ? "" : "text-dawn"}`}>{Number(r.delta) > 0 ? "+" : ""}{Number(r.delta).toFixed(2)}</td>
                <td className="text-right text-ink-soft">{Number(r.balance).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </>}
      </div>
    </div>
  );
}
