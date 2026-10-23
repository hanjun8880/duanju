"use client";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import type { Episode, Project } from "@/lib/contracts";
import { useWorkbench } from "@/lib/store";
import { type LlmUsage, type PlanProgress, progressText, usageText } from "./progress";

const STATUS: Record<string, string> = { outlined: "大纲", scripted: "已成剧本", storyboarded: "已拆分镜" };
const EPISODE_SECONDS = 90;
const COUNT_CHIPS = Array.from({ length: 13 }, (_, i) => i + 2); // 2..14, same spread as the counts people actually pick

export function ScriptRoom({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const episodes = useApi<Episode[]>(`/projects/${project.id}/episodes`);
  const { episode, setEpisode, setTab } = useWorkbench();
  const [count, setCount] = useState<number | null>(null);
  const [direction, setDirection] = useState("");
  const [mode, setMode] = useState<"generate" | "import">("generate");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [planning, setPlanning] = useState(false);
  const progress = useApi<PlanProgress>(planning ? `/projects/${project.id}/plan/progress` : null, 2000);
  const spend = useApi<LlmUsage>(`/projects/${project.id}/llm-usage`);
  const bible = project.series_bible_json ?? {};
  const list = episodes.data ?? [];
  const current = list.find((e) => e.episode_number === episode) ?? list[0];

  const run = async (label: string, fn: () => Promise<string>) => {
    setBusy(label); setMsg(null);
    try { setMsg({ ok: true, text: await fn() }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(null); episodes.reload(); spend.reload(); onChanged();
  };
  const plan = () => run("正在通读素材、锁定人物与分集…", async () => {
    if (list.length && !confirm("重新规划会替换现有的分集大纲和剧本（已有分镜不受影响）。继续吗？")) return "已取消";
    setPlanning(true);
    const r = await api<{ title: string; episodes: number; characters: number; props: number; scenes: number }>(
      `/projects/${project.id}/plan`, { method: "POST", json: { episode_count: count, direction: direction.trim() || null, replace: list.length > 0 } })
      .finally(() => setPlanning(false));
    setEpisode(1);
    return `《${r.title}》共 ${r.episodes} 集；已建立 ${r.characters} 个人物、${r.props} 件道具、${r.scenes} 处场景的定妆卡`;
  });
  const script = (n: number) => api<Episode>(`/projects/${project.id}/episodes/${n}/script`, { method: "POST" });
  const board = (n: number) => api<{ created: number }>(`/projects/${project.id}/episodes/${n}/storyboard`, { method: "POST" });
  const writeAll = () => run("逐集写剧本并拆分镜…", async () => {
    let done = 0;
    for (const e of list) {
      setBusy(`第 ${e.episode_number} / ${list.length} 集：${e.script_text ? "拆分镜" : "写剧本"}…`);
      try {
        if (!e.script_text) await script(e.episode_number);
        if (e.status !== "storyboarded") await board(e.episode_number);
      } catch (err) {
        throw new Error(`第 ${e.episode_number} 集失败：${(err as Error).message}（已完成 ${done} 集，可以单独重试这一集）`);
      }
      done++;
    }
    return `${done} 集剧本与分镜已就绪。定妆出图后就可以出片。`;
  });
  const patch = async (n: number, json: Partial<Episode>) => {
    try { await api(`/projects/${project.id}/episodes/${n}`, { method: "PATCH", json }); episodes.reload(); }
    catch (e) { setMsg({ ok: false, text: `第 ${n} 集未保存：${(e as Error).message}` }); }
  };

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
        {list.length > 0 && <button className="btn" disabled={!!busy} onClick={writeAll}>一键写完全部剧本并拆分镜</button>}
      </section>

      <section className="space-y-4" aria-label="生成或导入剧本">
        <div className="flex gap-2" role="group" aria-label="剧本来源">
          <button className={`btn ${mode === "generate" ? "" : "btn-quiet"}`} aria-pressed={mode === "generate"} onClick={() => setMode("generate")}>生成剧本</button>
          <button className={`btn ${mode === "import" ? "" : "btn-quiet"}`} aria-pressed={mode === "import"} onClick={() => setMode("import")}>导入剧本</button>
        </div>
        {mode === "generate" ? (
          <div className="space-y-3">
            <label className="block text-sm">创作方向（可选）
              <input className="field mt-1" placeholder="例如：都市甜宠、反转多、结局留悬念" value={direction} onChange={(e) => setDirection(e.target.value)} />
            </label>
            <div>
              <div className="mb-1 flex items-baseline justify-between text-sm text-ink-soft">
                <span>集数</span>
                <span>{count ? `每集约 ${EPISODE_SECONDS} 秒 · 合计约 ${Math.round(count * EPISODE_SECONDS / 60)} 分钟` : "集数按内容体量自定"}</span>
              </div>
              <div className="flex flex-wrap gap-2" role="group" aria-label="集数">
                <button className={`btn btn-quiet ${count === null ? "!border-ink" : ""}`} aria-pressed={count === null} onClick={() => setCount(null)}>AI 判断</button>
                {COUNT_CHIPS.map((n) => (
                  <button key={n} className={`btn btn-quiet ${count === n ? "!border-ink" : ""}`} aria-pressed={count === n} onClick={() => setCount(n)}>{n} 集</button>
                ))}
              </div>
            </div>
            <button className={`btn ${list.length ? "btn-quiet" : ""}`} disabled={!project.has_source_text || !!busy} onClick={plan}>{list.length ? "重新规划全剧" : "生成剧集圣经与分集大纲"}</button>
          </div>
        ) : <ImportScript project={project} busy={!!busy} hasExisting={list.length > 0} onDone={(m) => { setMsg(m); episodes.reload(); setEpisode(1); onChanged(); }} />}
      </section>

      {busy && <p className="text-dawn" role="status">{busy} 长篇需要几分钟，请不要关闭页面。</p>}
      {planning && progressText(progress.data) && <p className="text-sm text-ink-soft" role="status">{progressText(progress.data)}</p>}
      {usageText(spend.data) && <p className="text-xs text-ink-soft">{usageText(spend.data)}</p>}
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
            onPatch={(json) => patch(current.episode_number, json)}
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

type ImportMode = "as_is" | "ai_format" | "force_as_is";

function ImportScript({ project, busy, hasExisting, onDone }: {
  project: Project; busy: boolean; hasExisting: boolean; onDone: (m: { ok: boolean; text: string }) => void;
}) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [working, setWorking] = useState<ImportMode | "fetch" | "file" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const anyBusy = busy || working !== null;

  const fetchUrl = async () => {
    setWorking("fetch"); setErr(null);
    try {
      const r = await api<{ text: string }>("/source/fetch-url", { method: "POST", json: { url } });
      setText((t) => (t.trim() ? `${t}\n\n${r.text}` : r.text));
    } catch (e) { setErr((e as Error).message); }
    setWorking(null);
  };
  const importFile = async (file: File) => {
    setWorking("file"); setErr(null);
    try {
      if (/\.(pdf|docx)$/i.test(file.name)) {
        const fd = new FormData(); fd.append("file", file);
        setText((await api<{ text: string }>("/source/extract-file", { method: "POST", body: fd })).text);
      } else {
        const buf = await file.arrayBuffer();
        try { setText(new TextDecoder("utf-8", { fatal: true }).decode(buf)); } catch { setText(new TextDecoder("gb18030").decode(buf)); }
      }
    } catch (e) { setErr((e as Error).message); }
    setWorking(null);
  };
  const submit = async (mode: ImportMode) => {
    if (!text.trim()) return;
    if (hasExisting && !confirm("会替换现有的分集和剧本（已拆过分镜的集不受影响）。继续吗？")) return;
    setWorking(mode); setErr(null);
    try {
      const r = await api<{ episodes: number }>(`/projects/${project.id}/episodes/import`,
        { method: "POST", json: { text, mode, replace: true } });
      onDone({ ok: true, text: `已导入 ${r.episodes} 集剧本，可以在下面逐集查看、直接拆分镜。` });
    } catch (e) { setErr((e as Error).message); }
    setWorking(null);
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-soft">已经写好剧本？粘贴、从网页抓取或导入文件，按「第N集」自动分集；没有分集标记就整段当第一集。</p>
      <div className="flex flex-wrap items-center gap-2">
        <input className="field w-auto min-w-0 flex-1" inputMode="url" placeholder="https:// 粘贴剧本网页链接" value={url} onChange={(e) => setUrl(e.target.value)} />
        <button className="btn btn-quiet" disabled={!url.trim() || anyBusy} onClick={fetchUrl}>{working === "fetch" ? "抓取中…" : "抓取正文"}</button>
        <label className={`btn btn-quiet file-pick ${anyBusy ? "opacity-45" : ""}`}>{working === "file" ? "读取中…" : "导入文件"}
          <input type="file" accept=".txt,.pdf,.docx,text/plain" disabled={anyBusy} onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
        </label>
      </div>
      <textarea className="field h-56 leading-7" value={text} onChange={(e) => setText(e.target.value)}
        placeholder={"粘贴剧本或小说正文。已是场次剧本就用「直接采用原文」；小说请用「按原文整理成剧本」。"} />
      {err && <p className="text-seal" role="status">{err}</p>}
      <div className="flex flex-wrap gap-3">
        <button className="btn" disabled={!text.trim() || anyBusy} onClick={() => submit("as_is")}>{working === "as_is" ? "导入中…" : "直接采用原文"}</button>
        <button className="btn btn-quiet" disabled={!text.trim() || anyBusy} onClick={() => submit("ai_format")}>{working === "ai_format" ? "整理中…" : "按原文整理成剧本"}</button>
        <button className="btn btn-quiet" disabled={!text.trim() || anyBusy} onClick={() => submit("force_as_is")}>{working === "force_as_is" ? "导入中…" : "强制采用原文（不拆分集）"}</button>
      </div>
    </div>
  );
}
