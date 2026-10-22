"use client";
import { Fragment, useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import type { Job, Model, Project, Shot, Tier } from "@/lib/contracts";
import { estimateCredits, tierPlan } from "@/lib/estimate";
import { useWorkbench } from "@/lib/store";

const TIERS: { id: Tier; label: string; note: string }[] = [
  { id: "L0_animatic", label: "故事板", note: "本地" },
  { id: "L1_draft", label: "草稿", note: "480p" },
  { id: "L2_master", label: "母带", note: "1080p" },
  { id: "L3_post", label: "4K", note: "本地" },
];
const TYPE = { character: "人物", prop: "道具", scene: "场景" } as const;

export function StoryboardCanvas({ project, onSpent }: { project: Project; onSpent: () => void }) {
  const shots = useApi<Shot[]>(`/projects/${project.id}/shots`);
  const models = useApi<Model[]>("/models");
  const { chain, setChain } = useWorkbench();
  const [episode, setEpisode] = useState(1);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const episodes = useMemo(() => [...new Set((shots.data ?? []).map((s) => s.episode_number))].sort((a, b) => a - b), [shots.data]);
  const list = (shots.data ?? []).filter((s) => s.episode_number === episode);
  const byKey = useMemo(() => Object.fromEntries((models.data ?? []).map((m) => [m.model_key, m])), [models.data]);

  const cost = (tier: Tier, s: Shot) => {
    const plan = tierPlan(tier, s.duration);
    const m = byKey[plan.model_key];
    return m ? estimateCredits(m, plan.seconds, plan.resolution) : 0;
  };
  const total = (tier: Tier) => list.reduce((acc, s) => acc + cost(tier, s), 0);
  const seconds = list.reduce((a, s) => a + s.duration, 0);

  const render = async (s: Shot, tier: Tier) => {
    try {
      const job = await api<Job>(`/shots/${s.id}/render`, { method: "POST", json: { tier, chain } });
      setMsg({ ok: true, text: `第 ${s.shot_order} 镜已提交${TIERS.find((t) => t.id === tier)?.label}渲染，预扣 ${Number(job.credit_estimate).toFixed(1)} 积分` });
      onSpent(); shots.reload();
    } catch (e) { setMsg({ ok: false, text: `第 ${s.shot_order} 镜未提交：${(e as Error).message}` }); }
  };
  const savePrompt = async (s: Shot, shot_prompt: string) => {
    if (shot_prompt !== s.shot_prompt && shot_prompt.trim()) await api(`/shots/${s.id}`, { method: "PATCH", json: { shot_prompt } });
  };

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-6">
        <div className="flex items-end gap-4">
          <h2 className="text-2xl font-semibold">第{episode}集</h2>
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
          尾帧接力：上一镜的末帧作为下一镜的首帧
        </label>
      </div>
      {msg && <p className={`mb-4 ${msg.ok ? "text-ink" : "text-seal"}`} role="status">{msg.text}</p>}
      {list.length === 0 ? (
        <p className="text-ink-soft">本集还没有分镜。去「剧本与导入」拆一章小说或导入原片。</p>
      ) : (
        <div className="silk -mx-4 overflow-x-auto border-y border-celadon-deep/70 px-4 py-6 sm:-mx-8 sm:px-8" role="list" aria-label="分镜长卷">
          <div className="flex items-stretch">
            {list.map((s, i) => (
              <Fragment key={s.id}>
                {i > 0 && <Thread on={chain} passed={!!list[i - 1].last_frame_url} />}
                <ShotPanel shot={s} cost={cost} onRender={render} onPrompt={savePrompt} />
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
      {on && <span className="mt-1 text-xs text-dawn">{passed ? "已接" : "接力"}</span>}
    </div>
  );
}

function ShotPanel({ shot, cost, onRender, onPrompt }: {
  shot: Shot; cost: (t: Tier, s: Shot) => number; onRender: (s: Shot, t: Tier) => void; onPrompt: (s: Shot, p: string) => void;
}) {
  const cover = shot.last_frame_url ?? shot.first_frame_url ?? shot.refs.images?.[0];
  const done = TIERS.findIndex((t) => shot.status === `rendered_${t.id}`);
  return (
    <article role="listitem" className="flex w-[300px] shrink-0 flex-col" aria-label={`第 ${shot.shot_order} 镜`}>
      <div className="relative aspect-[9/16] max-h-[360px] w-full overflow-hidden border border-ink/70 bg-celadon">
        {cover ? <img src={cover} alt="" className="h-full w-full object-cover" /> :
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
      <label className="mt-2 block text-xs text-ink-soft">五段式镜头描述
        <textarea defaultValue={shot.shot_prompt} onBlur={(e) => onPrompt(shot, e.target.value)} className="field mt-1 h-24 text-sm leading-6 text-ink" />
      </label>
      <ol className="mt-3 grid grid-cols-4 border border-celadon-deep" aria-label="渲染阶梯">
        {TIERS.map((t, i) => (
          <li key={t.id} className={i ? "border-l border-celadon-deep" : ""}>
            <button onClick={() => onRender(shot, t.id)} title={`${t.label}，${t.note}`}
              className={`flex w-full flex-col items-center py-1.5 text-sm hover:bg-celadon ${i <= done ? "bg-ink text-paper hover:bg-ink" : ""}`}>
              <span>{t.label}</span>
              <span className="text-xs opacity-75">{cost(t.id, shot) ? cost(t.id, shot).toFixed(1) : "免费"}</span>
            </button>
          </li>
        ))}
      </ol>
    </article>
  );
}
