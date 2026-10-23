"use client";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import type { Episode, Project } from "@/lib/contracts";
import { useWorkbench } from "@/lib/store";

const STATUS: Record<string, string> = { outlined: "大纲", scripted: "已成剧本", storyboarded: "已拆分镜" };

export function ScriptRoom({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const episodes = useApi<Episode[]>(`/projects/${project.id}/episodes`);
  const { episode, setEpisode, setTab } = useWorkbench();
  const [count, setCount] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const bible = project.series_bible_json ?? {};
  const list = episodes.data ?? [];
  const current = list.find((e) => e.episode_number === episode) ?? list[0];

  const run = async (label: string, fn: () => Promise<string>) => {
    setBusy(label); setMsg(null);
    try { setMsg({ ok: true, text: await fn() }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(null); episodes.reload(); onChanged();
  };
  const plan = () => run("正在通读素材、锁定人物与分集…", async () => {
    if (list.length && !confirm("重新规划会替换现有的分集大纲和剧本（已有分镜不受影响）。继续吗？")) return "已取消";
    const r = await api<{ title: string; episodes: number; characters: number; props: number; scenes: number }>(
      `/projects/${project.id}/plan`, { method: "POST", json: { episode_count: count ? Number(count) : null, replace: list.length > 0 } });
    setEpisode(1);
    return `《${r.title}》共 ${r.episodes} 集；已建立 ${r.characters} 个人物、${r.props} 件道具、${r.scenes} 处场景的定妆卡`;
  });
  const script = (n: number) => api<Episode>(`/projects/${project.id}/episodes/${n}/script`, { method: "POST" });
  const board = (n: number) => api<{ created: number }>(`/projects/${project.id}/episodes/${n}/storyboard`, { method: "POST" });
  const writeAll = () => run("逐集写剧本并拆分镜…", async () => {
    let done = 0;
    for (const e of list) {
      setBusy(`第 ${e.episode_number} / ${list.length} 集：${e.script_text ? "拆分镜" : "写剧本"}…`);
      if (!e.script_text) await script(e.episode_number);
      if (e.status !== "storyboarded") await board(e.episode_number);
      done++;
    }
    return `${done} 集剧本与分镜已就绪。定妆出图后就可以出片。`;
  });
  const patch = (n: number, json: Partial<Episode>) => api(`/projects/${project.id}/episodes/${n}`, { method: "PATCH", json });

  return (
    <div className="space-y-8">
      <section className="flex flex-wrap items-end justify-between gap-6 border-b border-celadon-deep/60 pb-6">
        <div className="min-w-0 max-w-2xl">
          <h2 className="text-2xl font-semibold">{bible.title ?? project.title}</h2>
          <p className="mt-1 leading-7 text-ink-soft">{bible.logline ?? (project.has_source_text ? "素材已就绪。生成剧集圣经后，人物、道具、场景会自动建成定妆卡。" : "先在「素材」里导入小说、想法或原片。")}</p>
          {bible.characters && (
            <p className="mt-2 text-sm text-ink-soft">人物：{bible.characters.map((c) => `${c.name}（${c.role_tag}）`).join("，")}</p>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-sm">集数<input className="field mt-1 w-24" inputMode="numeric" placeholder="自动" value={count} onChange={(e) => setCount(e.target.value.replace(/\D/g, ""))} /></label>
          <button className={`btn ${list.length ? "btn-quiet" : ""}`} disabled={!project.has_source_text || !!busy} onClick={plan}>{list.length ? "重新规划全剧" : "生成剧集圣经与分集大纲"}</button>
          {list.length > 0 && <button className="btn" disabled={!!busy} onClick={writeAll}>一键写完全部剧本并拆分镜</button>}
        </div>
      </section>
      {busy && <p className="text-dawn" role="status">{busy} 长篇需要几分钟，请不要关闭页面。</p>}
      {msg && <p className={msg.ok ? "text-ink" : "text-seal"} role="status">{msg.text}</p>}

      {list.length > 0 && current && (
        <div className="grid gap-8 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <ol className="max-h-[70vh] space-y-1 overflow-y-auto" aria-label="分集">
            {list.map((e) => (
              <li key={e.episode_number}>
                <button onClick={() => setEpisode(e.episode_number)} aria-current={e.episode_number === current.episode_number}
                  className={`flex w-full items-baseline gap-3 border-l-2 px-3 py-2 text-left ${e.episode_number === current.episode_number ? "border-ink bg-paper" : "border-transparent hover:bg-paper/60"}`}>
                  <span className="shrink-0 whitespace-nowrap text-ink-soft">第{e.episode_number}集</span>
                  <span className="min-w-0 flex-1 truncate">{e.title}</span>
                  <span className={`shrink-0 text-xs ${e.status === "storyboarded" ? "text-ink" : "text-ink-soft"}`}>{STATUS[e.status] ?? e.status}</span>
                </button>
              </li>
            ))}
          </ol>
          <EpisodeDesk key={`${current.episode_number}-${current.script_text?.length ?? 0}`} ep={current} busy={!!busy}
            onPatch={(json) => patch(current.episode_number, json).then(episodes.reload)}
            onScript={() => run(`正在写第 ${current.episode_number} 集剧本…`, async () => { await script(current.episode_number); return `第 ${current.episode_number} 集剧本已写好，可以直接修改。`; })}
            onBoard={() => run(`正在拆第 ${current.episode_number} 集分镜…`, async () => {
              const r = await board(current.episode_number);
              setTab("storyboard");
              return `拆出 ${r.created} 个镜头`;
            })} />
        </div>
      )}
    </div>
  );
}

function EpisodeDesk({ ep, busy, onPatch, onScript, onBoard }: {
  ep: Episode; busy: boolean; onPatch: (j: Partial<Episode>) => void; onScript: () => void; onBoard: () => void;
}) {
  return (
    <article className="min-w-0 space-y-4" aria-label={`第 ${ep.episode_number} 集`}>
      <input className="field text-xl font-semibold" defaultValue={ep.title} aria-label="本集标题" onBlur={(e) => e.target.value !== ep.title && onPatch({ title: e.target.value })} />
      <label className="block text-sm text-ink-soft">本集大纲
        <textarea className="field mt-1 h-24 leading-7 text-ink" defaultValue={ep.synopsis} onBlur={(e) => e.target.value !== ep.synopsis && onPatch({ synopsis: e.target.value })} />
      </label>
      {ep.source_segments.length > 0 && <p className="text-xs text-ink-soft">改编自原文第 {ep.source_segments.join("、")} 段</p>}
      <label className="block text-sm text-ink-soft">分场剧本
        <textarea className="silk field mt-1 h-[28rem] leading-8 text-ink" defaultValue={ep.script_text ?? ""} placeholder="还没有剧本。可以让编剧生成，也可以直接在这里写。"
          onBlur={(e) => e.target.value !== (ep.script_text ?? "") && onPatch({ script_text: e.target.value })} />
      </label>
      <div className="flex flex-wrap gap-3">
        <button className={`btn ${ep.script_text ? "btn-quiet" : ""}`} disabled={busy} onClick={onScript}>{ep.script_text ? "重写本集剧本" : "生成本集剧本"}</button>
        <button className={`btn ${ep.script_text ? "" : "btn-quiet"}`} disabled={busy} onClick={onBoard}>{ep.status === "storyboarded" ? "重新拆分镜" : "拆成分镜"}</button>
      </div>
    </article>
  );
}
