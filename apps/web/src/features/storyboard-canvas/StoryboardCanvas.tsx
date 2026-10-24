"use client";
import { Fragment, useMemo, useState } from "react";
import { api, useApi, useBusySet } from "@/lib/api";
import type { Episode, Job, Model, Project, RenderRequest, Shot, ShotKind, StageRow, Tier } from "@/lib/contracts";
import { estimateCredits, tierPlan } from "@/lib/estimate";
import { useWorkbench } from "@/lib/store";

const TIERS: { id: Tier; label: string; note: string }[] = [
  { id: "L0_animatic", label: "故事板", note: "本地" },
  { id: "L1_draft", label: "草稿", note: "480p" },
  { id: "L2_master", label: "母带", note: "720p，按镜头类型选模型" },
  { id: "L3_post", label: "4K", note: "本地" },
];
const TYPE = { character: "人物", prop: "道具", scene: "场景" } as const;
const KIND: Record<ShotKind, string> = { dialogue: "对白", action: "动作", establishing: "空镜", insert: "插入" };

export function StoryboardCanvas({ project, onSpent }: { project: Project; onSpent: () => void }) {
  const shots = useApi<Shot[]>(`/projects/${project.id}/shots`, 5000);
  const outlines = useApi<Episode[]>(`/projects/${project.id}/episodes`);
  const models = useApi<Model[]>("/models");
  const stages = useApi<StageRow[]>("/admin/stages"); // admin routing overrides, so estimates follow the real model
  const { chain, setChain, episode, setEpisode } = useWorkbench();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const { busy: pending, run } = useBusySet<string>(); // shot ids, plus "all" while a whole-episode render is in flight
  const allBusy = pending.has("all");
  const episodes = useMemo(() => [...new Set([...(shots.data ?? []).map((s) => s.episode_number),
    ...(outlines.data ?? []).map((e) => e.episode_number)])].sort((a, b) => a - b), [shots.data, outlines.data]);
  const title = outlines.data?.find((e) => e.episode_number === episode)?.title;
  const list = (shots.data ?? []).filter((s) => s.episode_number === episode);
  const byKey = useMemo(() => Object.fromEntries((models.data ?? []).map((m) => [m.model_key, m])), [models.data]);

  const cost = (tier: Tier, s: Shot) => {
    const plan = tierPlan(tier, s.duration, { shotKind: s.shot_kind, stages: stages.data ?? undefined });
    const m = byKey[plan.model_key];
    return m ? estimateCredits(m, plan.seconds, plan.resolution) : 0;
  };
  const total = (tier: Tier) => list.reduce((acc, s) => acc + cost(tier, s), 0);
  const seconds = list.reduce((a, s) => a + s.duration, 0);

  const render = (s: Shot, tier: Tier, force = false) => {
    if (allBusy) return;
    run(s.id, async () => {
      try {
        const request: RenderRequest = { tier, chain, force };
        const job = await api<Job>(`/shots/${s.id}/render`, { method: "POST", json: request });
        setMsg({ ok: true, text: job.cached_from
          ? `第 ${s.shot_order} 镜复用了已有视频，本次没有调用生成模型。效果不满意可选择下方「重新生成」。`
          : `第 ${s.shot_order} 镜已提交${force ? "重新生成" : "渲染"}（${TIERS.find((t) => t.id === tier)?.label}），预扣 ${Number(job.credit_estimate).toFixed(1)} 积分` });
        onSpent(); shots.reload();
      } catch (e) { setMsg({ ok: false, text: `第 ${s.shot_order} 镜未提交：${(e as Error).message}` }); }
    });
  };
  const renderAll = (tier: Tier) => {
    run("all", async () => {
      try {
        const r = await api<{ jobs: Job[]; mode: string }>(`/projects/${project.id}/episodes/${episode}/render`, { method: "POST", json: { tier, chain } });
        const spent = r.jobs.reduce((a, j) => a + Number(j.credit_estimate), 0);
        setMsg({ ok: true, text: r.mode === "sequential"
          ? `整集母带按尾帧接力逐镜出片，已提交第 1 镜（预扣 ${spent.toFixed(1)} 积分），后续镜头自动接上`
          : `已提交 ${r.jobs.length} 个镜头${TIERS.find((t) => t.id === tier)?.label}，预扣 ${spent.toFixed(1)} 积分` });
        onSpent(); shots.reload();
      } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    });
  };
  const patch = async (s: Shot, json: Record<string, unknown>) => {
    try { await api(`/shots/${s.id}`, { method: "PATCH", json }); shots.reload(); }
    catch (e) { setMsg({ ok: false, text: `第 ${s.shot_order} 镜未保存：${(e as Error).message}` }); }
  };
  const select = async (s: Shot, job_id: string | null) => {
    try { await api(`/shots/${s.id}/select`, { method: "POST", json: { job_id } }); shots.reload(); }
    catch (e) { setMsg({ ok: false, text: `第 ${s.shot_order} 镜选片失败：${(e as Error).message}` }); }
  };
  const savePrompt = (s: Shot, shot_prompt: string) => {
    if (shot_prompt === s.shot_prompt || !shot_prompt.trim()) return;
    run(s.id, async () => {
      try { await api(`/shots/${s.id}`, { method: "PATCH", json: { shot_prompt } }); shots.reload(); }
      catch (e) { setMsg({ ok: false, text: `第 ${s.shot_order} 镜描述未保存：${(e as Error).message}` }); }
    });
  };

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-6">
        <div className="flex min-w-0 flex-wrap items-end gap-4">
          <h2 className="min-w-0 break-words text-2xl font-semibold">第{episode}集{title ? `　${title}` : ""}</h2>
          {episodes.length > 1 && (
            <select className="field w-auto py-1" value={episode} onChange={(e) => setEpisode(Number(e.target.value))} aria-label="选择集数">
              {episodes.map((n) => <option key={n} value={n}>第 {n} 集</option>)}
            </select>
          )}
          <span className="pb-0.5 text-ink-soft">{list.length} 镜，{seconds} 秒</span>
        </div>
        <dl className="flex gap-6 text-sm" aria-label="本集渲染预算">
          <div><dt className="text-ink-soft">故事板与 4K</dt><dd className="text-lg">0 积分</dd></div>
          <div><dt className="text-ink-soft">全部草稿</dt><dd className="text-lg">{total("L1_draft").toFixed(1)} 积分</dd></div>
          <div><dt className="text-ink-soft">全部母带</dt><dd className="text-lg font-semibold">{total("L2_master").toFixed(1)} 积分</dd></div>
        </dl>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={chain} onChange={(e) => setChain(e.target.checked)} />
          尾帧接力：只对标为「接上一镜」的镜头生效，其余镜头用参考图出片
        </label>
      </div>
      {list.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-3" role="group" aria-label="整集出片">
          <span className="text-ink-soft">整集出片</span>
          <button className="btn btn-quiet" disabled={allBusy} onClick={() => renderAll("L0_animatic")}>故事板（免费）</button>
          <button className="btn btn-quiet" disabled={allBusy} onClick={() => renderAll("L1_draft")}>草稿 {total("L1_draft").toFixed(1)}</button>
          <button className="btn" disabled={allBusy} onClick={() => renderAll("L2_master")}>{allBusy ? "提交中…" : `母带 ${total("L2_master").toFixed(1)}`}</button>
        </div>
      )}
      {msg && <p className={`mb-4 ${msg.ok ? "text-ink" : "text-seal"}`} role="status">{msg.text}</p>}
      {shots.error ? (
        <div className="space-y-3" role="alert"><p className="text-seal">分镜读取失败：{shots.error}</p>
          <button type="button" className="btn btn-quiet" onClick={shots.reload}>重试读取分镜</button></div>
      ) : shots.loading ? (
        <p className="text-ink-soft">分镜加载中…</p>
      ) : list.length === 0 ? (
        <p className="text-ink-soft">本集还没有分镜。去「剧本」里写好本集剧本并拆成分镜。</p>
      ) : (
        <div className="silk -mx-4 overflow-x-auto border-y border-celadon-deep/70 px-4 py-6 sm:-mx-8 sm:px-8" role="list" aria-label="分镜长卷">
          <div className="flex items-stretch">
            {list.map((s, i) => (
              <Fragment key={s.id}>
                {i > 0 && <Thread on={chain && !!s.continuous_from_prev} passed={!!list[i - 1].last_frame_url} />}
                <ShotPanel shot={s} first={i === 0} cost={cost} onRender={render} onPrompt={savePrompt}
                  onPatch={patch} onSelect={select} busy={pending.has(s.id) || allBusy} />
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Thread({ on, passed }: { on: boolean; passed: boolean }) {
  return (
    <div className="flex w-14 shrink-0 flex-col items-center justify-center" aria-hidden>
      <div className={`h-px w-full ${on ? (passed ? "bg-dawn" : "border-t border-dashed border-dawn") : "bg-celadon-deep/50"}`} />
      {on ? <span className="mt-1 text-xs text-dawn">{passed ? "已接" : "接力"}</span>
        : <span className="mt-1 text-xs text-ink-soft">切</span>}
    </div>
  );
}

function ShotPanel({ shot, first, cost, onRender, onPrompt, onPatch, onSelect, busy }: {
  shot: Shot; first: boolean; cost: (t: Tier, s: Shot) => number; onRender: (s: Shot, t: Tier, force?: boolean) => void;
  onPrompt: (s: Shot, p: string) => void; onPatch: (s: Shot, j: Record<string, unknown>) => void;
  onSelect: (s: Shot, jobId: string | null) => void; busy: boolean;
}) {
  const cover = shot.last_frame_url ?? (shot.keyframe_status === "approved" ? shot.keyframe_url : null) ?? shot.refs.images?.[0];
  const takes = shot.takes ?? [];
  const done = TIERS.findIndex((t) => shot.status === `rendered_${t.id}`);
  return (
    <article role="listitem" className="flex w-[300px] shrink-0 flex-col" aria-label={`第 ${shot.shot_order} 镜`}>
      <div className="relative aspect-[9/16] max-h-[360px] w-full overflow-hidden border border-ink/70 bg-celadon">
        {shot.preview_url ? <video src={shot.preview_url} controls preload="metadata" poster={cover} className="h-full w-full bg-ink object-cover" /> :
          cover ? <img src={cover} alt="" className="h-full w-full object-cover" /> :
          <p className="p-4 text-sm text-ink-soft">尚无画面。先在定妆里锁定出场人物与场景。</p>}
        <span className="absolute top-0 left-0 bg-ink px-2 py-0.5 text-paper">{String(shot.shot_order).padStart(2, "0")}</span>
        <span className="absolute top-0 right-0 bg-paper/90 px-2 py-0.5 text-sm">{shot.duration} 秒</span>
      </div>
      <div className="min-h-[5.5rem]">
        <p className="mt-3 text-[15px] leading-7">{shot.dialogue_transcript ? `「${shot.dialogue_transcript}」` : <span className="text-ink-soft">无台词</span>}</p>
        {shot.refs.assets && shot.refs.assets.length > 0 && (
          <p className="mt-1 text-xs leading-5 text-ink-soft">{shot.refs.assets.map((a) => `图${a.range[0]}${a.range[1] > a.range[0] ? `至${a.range[1]}` : ""} ${a.name}（${TYPE[a.type]}）`).join("，")}</p>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1 text-ink-soft">类型
          <select className="field w-auto py-0.5 text-xs text-ink" value={shot.shot_kind ?? "dialogue"}
            onChange={(e) => onPatch(shot, { shot_kind: e.target.value })} aria-label="镜头类型">
            {(Object.keys(KIND) as ShotKind[]).map((k) => <option key={k} value={k}>{KIND[k]}</option>)}
          </select>
        </label>
        {!first && (
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={!!shot.continuous_from_prev}
              onChange={(e) => onPatch(shot, { continuous_from_prev: e.target.checked })} />
            接上一镜
          </label>
        )}
        <span className="text-ink-soft" title="关键帧审批通过后作为首帧">
          关键帧：{({ none: "无", pending: "生成中", ready: "待审", approved: "已批", rejected: "已退" } as Record<string, string>)[shot.keyframe_status ?? "none"] ?? shot.keyframe_status}
        </span>
      </div>
      <label className="mt-2 block text-xs text-ink-soft">五段式镜头描述
        <textarea defaultValue={shot.shot_prompt} onBlur={(e) => onPrompt(shot, e.target.value)} className="field mt-1 h-24 text-sm leading-6 text-ink" />
      </label>
      <ol className="mt-3 grid grid-cols-4 border border-celadon-deep" aria-label="渲染阶梯">
        {TIERS.map((t, i) => (
          <li key={t.id} className={i ? "border-l border-celadon-deep" : ""}>
            <button type="button" onClick={() => onRender(shot, t.id)} disabled={busy} aria-busy={busy} title={`${t.label}，${t.note}；相同参数优先复用已有结果`}
              className={`flex w-full flex-col items-center py-1.5 text-sm hover:bg-celadon disabled:opacity-50 ${i <= done ? "bg-ink text-paper hover:bg-ink" : ""}`}>
              <span>{t.label}</span>
              <span className="text-xs opacity-75">{cost(t.id, shot) ? cost(t.id, shot).toFixed(1) : "免费"}</span>
            </button>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-xs leading-5 text-ink-soft">相同参数优先复用已有结果。妆效不满意，先调整定妆参考或本镜描述，再重出这一镜。</p>
      {(takes.length > 0 || shot.preview_url) && (
        <div className="shot-regenerate mt-3 border border-celadon-deep p-3" role="group" aria-label={`第 ${shot.shot_order} 镜重新生成`}>
          <p className="text-xs leading-5 text-ink-soft">重新生成会跳过缓存并重新计费，仅重做本镜，旧版本保留供选片。</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {(["L1_draft", "L2_master"] as const).map((tier) => (
              <button key={tier} type="button" className="btn btn-quiet px-2 text-xs" disabled={busy} aria-busy={busy}
                onClick={() => onRender(shot, tier, true)}>
                {busy ? "处理中…" : `重新生成${tier === "L1_draft" ? "草稿" : "母带"}`} · <span className="tabular-nums">{cost(tier, shot).toFixed(1)}</span> 积分
              </button>
            ))}
          </div>
        </div>
      )}
      {takes.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs" aria-label="已出片的版本">
          {takes.map((t) => {
            const chosen = shot.selected_job_id === t.job_id;
            return (
              <li key={t.job_id} className="flex items-center justify-between gap-2">
                <a href={t.url} target="_blank" rel="noreferrer" className="min-w-0 break-all underline">
                  {TIERS.find((x) => x.id === t.render_tier)?.label ?? t.render_tier}{t.final ? "（终版）" : ""} · {t.model_key}
                </a>
                <button className="btn btn-quiet px-2 py-0.5 text-xs" onClick={() => onSelect(shot, chosen ? null : t.job_id)}>
                  {chosen ? "已选用（取消）" : "选用"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}
