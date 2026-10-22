"use client";
import { useEffect, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import type { Job, LedgerRow, Model } from "@/lib/contracts";

const REASON: Record<string, string> = {
  pre_deduct: "预扣", settle_refund: "结算退回", settle_extra_charge: "结算补扣", provider_5xx_refund: "供应商故障退回",
  provider_4xx_refund: "参数被拒退回", timeout_refund: "超时退回", policy_reject_refund: "内容策略退回",
  cancel_refund: "取消退回", manual_topup: "充值",
};
const STATUS: Record<string, string> = {
  queued: "排队", processing: "渲染中", success: "完成", failed: "失败", cancelled: "已取消", dead_letter: "死信",
};

export function AdminLedger({ onClose }: { onClose: () => void }) {
  const models = useApi<Model[]>("/admin/models");
  const jobs = useApi<Job[]>("/admin/jobs?limit=30");
  const ledger = useApi<LedgerRow[]>("/ledger?limit=30");
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
    models.reload(); jobs.reload(); ledger.reload();
  };

  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-ink/30" onClick={onClose}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="ledger-title"
        className="h-full w-full max-w-2xl overflow-y-auto bg-paper px-6 py-6 shadow-[-12px_0_32px_rgb(34_48_47/0.18)]" onClick={(e) => e.stopPropagation()}>
        <div className="mb-6 flex items-baseline justify-between">
          <h2 id="ledger-title" className="text-2xl font-semibold">账本与模型</h2>
          <button className="btn btn-quiet" onClick={onClose}>关闭</button>
        </div>
        {err && <p className="mb-4 text-seal" role="alert">{err}</p>}

        <h3 className="mb-2 font-semibold">模型开关与单价</h3>
        <table className="mb-8 w-full text-sm">
          <thead><tr className="text-left text-ink-soft"><th className="py-1 font-normal">模型</th><th className="font-normal">积分／单位</th><th className="font-normal">降级到</th><th className="font-normal">启用</th></tr></thead>
          <tbody>
            {models.data?.map((m) => (
              <tr key={m.model_key} className="border-t border-celadon">
                <td className="py-1.5">{m.display_name}<div className="text-xs text-ink-soft">{m.model_key}</div></td>
                <td><input aria-label={`${m.model_key} 单价`} className="field w-20 py-0.5" defaultValue={Number(m.credits_per_unit)}
                  onBlur={(e) => e.target.value !== String(Number(m.credits_per_unit)) && act(() => api(`/admin/models/${m.model_key}`, { method: "PATCH", json: { credits_per_unit: e.target.value } }))} /></td>
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
      </div>
    </div>
  );
}
