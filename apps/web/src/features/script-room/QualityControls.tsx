"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ProjectQualityTiers, StageTiers } from "@/lib/contracts";

const ROLES = [
  { stage: "summarize", label: "素材阅读", note: "只为未缓存的原文片段生成摘要" },
  { stage: "plan", label: "全剧规划", note: "人物设定、剧情走向与分集大纲" },
  { stage: "script", label: "分集编剧", note: "只处理当前集原文与前情摘要" },
  { stage: "storyboard", label: "分镜导演", note: "将当前集剧本拆成镜头" },
  { stage: "reformat", label: "剧本整理", note: "导入时整理格式，保留原剧情" },
] as const;
const LABELS: Record<string, string> = { draft: "经济", standard: "标准", high: "精修", overseas: "海外题材" };

export function QualityControls({ projectId, disabled, onBlockedChange }: {
  projectId: string; disabled: boolean; onBlockedChange: (blocked: boolean) => void;
}) {
  const [options, setOptions] = useState<StageTiers | null>(null);
  const [saved, setSaved] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const lock = useRef(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const ready = !!options && ROLES.every(({ stage }) => !!options[stage]?.standard);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    Promise.all([
      api<StageTiers>("/stage-tiers", { signal: controller.signal }),
      api<ProjectQualityTiers>(`/projects/${projectId}/quality-tiers`, { signal: controller.signal }),
    ]).then(([catalog, project]) => {
      if (controller.signal.aborted) return;
      setOptions(catalog); setSaved(project.tiers); setDraft(project.tiers); setLoading(false);
    }).catch((err: Error) => {
      if (!controller.signal.aborted) { setError(err.message); setLoading(false); }
    });
    return () => controller.abort();
  }, [projectId, revision]);

  useEffect(() => {
    onBlockedChange(loading || saving || dirty || !ready);
  }, [loading, saving, dirty, ready, onBlockedChange]);

  const save = async () => {
    if (lock.current || disabled || !ready) return;
    lock.current = true; setSaving(true); setError(null); setMessage(null);
    try {
      const result = await api<ProjectQualityTiers>(`/projects/${projectId}/quality-tiers`, {
        method: "PUT", json: { tiers: draft },
      });
      setSaved(result.tiers); setDraft(result.tiers);
      setMessage("已保存。后续生成使用这些档位，已有剧本和缓存保持可用。");
    } catch (err) { setError(`未保存，仍沿用上次设置：${(err as Error).message}`); }
    finally { lock.current = false; setSaving(false); }
  };

  return (
    <section className="cost-controls min-w-0 border border-celadon-deep bg-paper p-4" aria-labelledby="quality-title" aria-busy={loading || saving}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 id="quality-title" className="text-[clamp(1.125rem,2vw,1.25rem)] font-semibold tracking-[-0.02em] [text-wrap:balance]">岗位与生成成本</h3>
          <p className="mt-1 max-w-[65ch] text-sm leading-6 text-ink-soft [text-wrap:pretty]">按环节分配模型，先用经济档试写单集，再精修满意的内容。视频费用另计；经济档需配置对应供应商密钥。</p>
        </div>
        <button type="button" className="btn btn-quiet shrink-0" disabled={loading || saving || disabled || !ready}
          onClick={() => { setDraft({ ...draft, ...Object.fromEntries(ROLES.map(({ stage }) => [stage, "draft"])) }); setMessage(null); }}>
          全部设为经济档
        </button>
      </div>
      {loading ? (
        <>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3" role="status" aria-label="正在加载岗位设置">
            {ROLES.map(({ stage }) => <div key={stage} className="h-44 border border-celadon-deep/60 bg-celadon/30 motion-safe:animate-pulse" />)}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3" aria-hidden="true">
            <button type="button" className="btn" disabled>保存岗位设置</button>
            <p className="text-xs leading-5 text-ink-soft">设置仅影响后续请求，不会自动重跑任何岗位。</p>
          </div>
        </>
      ) : !ready ? (
        <div className="mt-4 min-h-40 space-y-3" role="status">
          <p className="text-sm text-ink-soft">{error ? `岗位设置读取失败：${error}` : "尚无可用档位，请先配置服务端模型分工。"}</p>
          <button type="button" className="btn btn-quiet" onClick={() => setRevision((n) => n + 1)}>重新读取设置</button>
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {ROLES.map(({ stage, label, note }) => {
              const selected = draft[stage] ?? "standard";
              const option = options?.[stage]?.[selected];
              const overridden = option && (option.model !== option.effective_model || option.provider !== option.effective_provider);
              return (
                <div key={stage} className="h-44 min-w-0 border border-celadon-deep/60 p-3">
                  <label className="block text-sm font-semibold">{label}
                    <select className="field mt-2 min-w-0 font-normal" value={selected} disabled={saving || disabled}
                      onChange={(event) => { setDraft({ ...draft, [stage]: event.target.value }); setMessage(null); }}>
                      {!option && <option value={selected}>原设置不可用，请重新选择</option>}
                      {Object.keys(options?.[stage] ?? {}).map((tier) => <option key={tier} value={tier}>{LABELS[tier] ?? tier}</option>)}
                    </select>
                  </label>
                  <p className="mt-2 line-clamp-2 break-all text-xs leading-5 text-ink-soft" title={`${option?.effective_provider} · ${option?.effective_model}`}>{option?.effective_provider} · {option?.effective_model}</p>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-ink-soft">{overridden ? "部署设置已覆盖该档位，实际使用上方模型。" : note}</p>
                </div>
              );
            })}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" className="btn" aria-busy={saving} disabled={!dirty || saving || disabled} onClick={save}>
              {saving ? "保存中…" : error ? "重试保存" : "保存岗位设置"}
            </button>
            {dirty && <button type="button" className="btn btn-quiet" disabled={saving || disabled}
              onClick={() => { setDraft(saved); setError(null); setMessage(null); }}>撤销修改</button>}
            <p className="text-xs leading-5 text-ink-soft" role="status">{saving ? "正在保存，请稍候。" : dirty ? "有未保存的调整，保存后再开始生成。" : message ?? "设置仅影响后续请求，不会自动重跑任何岗位。"}</p>
          </div>
          {error && <p className="mt-3 break-words text-sm text-seal" role="alert">{error}</p>}
        </>
      )}
    </section>
  );
}
