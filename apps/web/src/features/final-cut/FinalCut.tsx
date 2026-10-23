"use client";
import { useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import type { Episode, Output, Project, Shot } from "@/lib/contracts";

export function FinalCut({ project }: { project: Project }) {
  const episodes = useApi<Episode[]>(`/projects/${project.id}/episodes`);
  const shots = useApi<Shot[]>(`/projects/${project.id}/shots`, 8000);
  const outputs = useApi<Output[]>(`/projects/${project.id}/outputs`, 5000);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const ready = useMemo(() => {
    const m = new Map<number, { done: number; total: number }>();
    for (const s of shots.data ?? []) {
      const r = m.get(s.episode_number) ?? { done: 0, total: 0 };
      r.total++; if (s.preview_url) r.done++;
      m.set(s.episode_number, r);
    }
    return m;
  }, [shots.data]);
  const numbers = [...new Set([...(episodes.data ?? []).map((e) => e.episode_number), ...ready.keys()])].sort((a, b) => a - b);
  const latest = (n: number) => outputs.data?.find((o) => o.kind === "episode" && o.meta.episode_number === n);
  const series = outputs.data?.find((o) => o.kind === "series");

  const assemble = async (episode_number: number | null) => {
    try {
      await api(`/projects/${project.id}/assemble`, { method: "POST", json: { episode_number } });
      setMsg({ ok: true, text: episode_number ? `第 ${episode_number} 集正在本地合成` : "整部连续剧正在本地合成" });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };

  return (
    <div className="space-y-10">
      <section className="flex flex-wrap items-end justify-between gap-6 border-b border-celadon-deep/60 pb-6">
        <div className="max-w-2xl">
          <h2 className="text-2xl font-semibold">成片</h2>
          <p className="mt-1 leading-7 text-ink-soft">每个镜头取最高一档的成品（4K、母带、草稿、故事板依次优先），按分镜顺序拼成一集；整部按集数顺序串起来。同时导出 OTIO 时间线，可在剪辑软件里继续精修。</p>
        </div>
        <button className="btn" disabled={!outputs.data?.some((o) => o.kind === "episode")} onClick={() => assemble(null)}>导出整部连续剧</button>
      </section>
      {msg && <p className={msg.ok ? "text-ink" : "text-seal"} role="status">{msg.text}</p>}
      {series && (
        <section aria-labelledby="series">
          <h3 id="series" className="mb-3 text-lg font-semibold">整部（第 {series.meta.episodes?.join("、")} 集）</h3>
          <video src={series.url} controls preload="metadata" className="aspect-[9/16] h-[60vh] max-w-full border border-ink/70 bg-ink object-contain" />
          <a className="mt-2 inline-block text-sm underline" href={series.url} download>下载整部 mp4</a>
        </section>
      )}
      {numbers.length === 0 ? <p className="text-ink-soft">还没有分集。先在「剧本」里生成分集并拆分镜。</p> : (
        <ul className="grid gap-8 sm:grid-cols-2 xl:grid-cols-4">
          {numbers.map((n) => {
            const r = ready.get(n) ?? { done: 0, total: 0 };
            const out = latest(n);
            const title = episodes.data?.find((e) => e.episode_number === n)?.title;
            return (
              <li key={n} className="flex flex-col">
                <div className="mb-2 flex items-baseline justify-between gap-2">
                  <span className="font-semibold">第{n}集{title ? `　${title}` : ""}</span>
                  <span className="text-sm text-ink-soft">{r.done}/{r.total} 镜已出片</span>
                </div>
                <div className="aspect-[9/16] max-h-[420px] border border-ink/70 bg-celadon">
                  {out ? <video src={out.url} controls preload="metadata" className="h-full w-full bg-ink object-contain" /> :
                    <p className="p-4 text-sm text-ink-soft">{r.done ? "镜头已就绪，可以合成本集。" : "本集还没有出片的镜头。"}</p>}
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <button className="btn btn-quiet" disabled={!r.done} onClick={() => assemble(n)}>{out ? "重新合成" : "合成本集"}</button>
                  {out && <a className="text-sm underline" href={out.url} download>下载</a>}
                  {out && out.meta.shots_used !== undefined && out.meta.shots_used < (out.meta.shots_total ?? 0) &&
                    <span className="text-xs text-seal">缺 {(out.meta.shots_total ?? 0) - out.meta.shots_used} 镜</span>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
