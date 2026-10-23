"use client";
import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import type { ContentMode, Project } from "@/lib/contracts";
import { useWorkbench } from "@/lib/store";

const RATIOS = ["9:16", "16:9", "1:1", "3:4", "4:3", "21:9"] as const;

export function ProjectImporter({ projects, onChanged }: { projects: Project[]; onChanged: () => void }) {
  const { projectId, setProject } = useWorkbench();
  const current = projects.find((p) => p.id === projectId);
  return (
    <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
      <NewProject onCreated={(p) => { setProject(p.id); onChanged(); }} />
      {current ? <SourcePanel key={current.id} project={current} onChanged={onChanged} /> :
        <p className="text-ink-soft">新建项目后，在这里导入小说、写下一句想法，或上传一段原片。</p>}
    </div>
  );
}

function NewProject({ onCreated }: { onCreated: (p: Project) => void }) {
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<ContentMode>("standard_sfw");
  const [ratio, setRatio] = useState<(typeof RATIOS)[number]>("9:16");
  const [adult, setAdult] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    try {
      const p = await api<Project>("/projects", { method: "POST", json: { title, content_mode: mode, aspect_ratio: ratio, confirm_18plus: adult } });
      setTitle(""); setErr(null); onCreated(p);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <section aria-labelledby="new-project">
      <h2 id="new-project" className="mb-4 text-xl font-semibold">新建剧集项目</h2>
      <div className="space-y-4">
        <label className="block">剧名<input className="field mt-1" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：雾港来信" /></label>
        <label className="block">画幅
          <select className="field mt-1" value={ratio} onChange={(e) => setRatio(e.target.value as (typeof RATIOS)[number])}>
            {RATIOS.map((r) => <option key={r} value={r}>{r}{r === "9:16" ? "（竖屏短剧）" : ""}</option>)}
          </select>
        </label>
        <fieldset>
          <legend className="mb-1">内容模式</legend>
          <div className="flex gap-2">
            {(["standard_sfw", "uncensored_nsfw"] as const).map((m) => (
              <button key={m} type="button" onClick={() => setMode(m)} aria-pressed={mode === m}
                className={`btn ${mode === m ? "" : "btn-quiet"} ${m === "uncensored_nsfw" && mode === m ? "!border-seal !bg-seal" : ""}`}>
                {m === "standard_sfw" ? "常规短剧" : "无审查（18+）"}
              </button>
            ))}
          </div>
          {mode === "uncensored_nsfw" && (
            <label className="mt-3 flex items-start gap-2 text-sm text-seal">
              <input type="checkbox" className="mt-1" checked={adult} onChange={(e) => setAdult(e.target.checked)} />
              我确认已满 18 周岁，本项目只出现虚构的成年角色。涉及未成年人的内容会被直接拦截并记录。
            </label>
          )}
        </fieldset>
        {err && <p className="text-seal">{err}</p>}
        <button className="btn" disabled={!title || (mode === "uncensored_nsfw" && !adult)} onClick={create}>创建项目</button>
      </div>
    </section>
  );
}

type Kind = "novel" | "idea" | "video";
const KINDS: { id: Kind; label: string; hint: string }[] = [
  { id: "novel", label: "小说原文", hint: "整本或一卷都行。按「第X章」自动分段，每集只取对应章节改编。" },
  { id: "idea", label: "一句想法", hint: "一两句话的创意，从人物到分集都由编剧生成。" },
  { id: "video", label: "原片二创", hint: "本机切镜、转写台词、抽关键帧，再由视觉模型识别画面，变成可改编的文本。" },
];

/** Novels often come as GB18030 .txt; try strict UTF-8 first. Word/PDF go to the server to extract. */
async function readText(file: File): Promise<string> {
  if (/\.(pdf|docx)$/i.test(file.name)) {
    const fd = new FormData(); fd.append("file", file);
    return (await api<{ text: string }>("/source/extract-file", { method: "POST", body: fd })).text;
  }
  const buf = await file.arrayBuffer();
  try { return new TextDecoder("utf-8", { fatal: true }).decode(buf); } catch { return new TextDecoder("gb18030").decode(buf); }
}

function SourcePanel({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const { setTab } = useWorkbench();
  const [watch, setWatch] = useState(project.status === "remixing");
  const detail = useApi<Project & { source_novel_text?: string }>(`/projects/${project.id}`, watch ? 5000 : undefined);
  useEffect(() => {
    if (watch && detail.data && detail.data.status !== "remixing") { setWatch(false); onChanged(); }
  }, [watch, detail.data, onChanged]);
  const [kind, setKind] = useState<Kind>("novel");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    try { setMsg({ ok: true, text: await fn() }); onChanged(); detail.reload(); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const save = () => run(async () => {
    await api(`/projects/${project.id}`, { method: "PATCH", json: { source_novel_text: text } });
    return `已保存 ${text.length.toLocaleString()} 字。下一步去「剧本」生成剧集圣经与分集大纲。`;
  });
  const fetchUrl = () => run(async () => {
    const r = await api<{ text: string }>("/source/fetch-url", { method: "POST", json: { url } });
    setText((t) => (t.trim() ? `${t}\n\n${r.text}` : r.text));
    return `已抓取 ${r.text.length.toLocaleString()} 字正文，记得点「保存为本剧素材」。`;
  });
  const importFile = (file: File) => run(async () => {
    setText(await readText(file));
    return `已读入《${file.name}》，记得点「保存为本剧素材」。`;
  });
  const [lastVideoPath, setLastVideoPath] = useState<string | null>(null);
  const retryRemix = (video_path: string) => run(async () => {
    await api(`/projects/${project.id}/remix`, { method: "POST", json: { video_path } });
    setWatch(true);
    return "已重新提交拆解，不用重新上传。";
  });
  const remix = (file: File) => run(async () => {
    const fd = new FormData(); fd.append("file", file);
    const up = await api<{ path: string }>("/uploads", { method: "POST", body: fd });
    setLastVideoPath(up.path);
    await api(`/projects/${project.id}/remix`, { method: "POST", json: { video_path: up.path } });
    setWatch(true);
    return "原片已进入本地拆解队列。完成后，逐镜头的画面描述和台词会成为本项目的素材文本。";
  });

  return (
    <section aria-labelledby="source" className="space-y-6">
      <h2 id="source" className="text-xl font-semibold">《{project.title}》的素材</h2>
      <div className="flex flex-wrap gap-2" role="group" aria-label="素材类型">
        {KINDS.map((k) => (
          <button key={k.id} className={`btn ${kind === k.id ? "" : "btn-quiet"}`} aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>{k.label}</button>
        ))}
      </div>
      <p className="text-sm text-ink-soft">{KINDS.find((k) => k.id === kind)?.hint}</p>
      {kind === "video" ? (
        <label className="btn btn-quiet file-pick">选择原片视频
          <input type="file" accept="video/*" disabled={busy} onChange={(e) => e.target.files?.[0] && remix(e.target.files[0])} />
        </label>
      ) : (
        <div className="space-y-3">
          {kind === "novel" && (
            <div className="flex flex-wrap items-center gap-2">
              <input className="field w-auto min-w-0 flex-1" inputMode="url" placeholder="https:// 粘贴小说/剧本网页链接" value={url} onChange={(e) => setUrl(e.target.value)} />
              <button className="btn btn-quiet" disabled={!url.trim() || busy} onClick={fetchUrl}>抓取正文</button>
              <label className={`btn btn-quiet file-pick ${busy ? "opacity-45" : ""}`}>导入文件
                <input type="file" accept=".txt,.pdf,.docx,text/plain" disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
              </label>
            </div>
          )}
          <textarea className={`field ${kind === "novel" ? "h-64" : "h-28"} leading-7`} value={text} onChange={(e) => setText(e.target.value)}
            placeholder={kind === "novel" ? "粘贴小说正文，或用上方「抓取正文」「导入文件」" : "例如：一个落魄画师在宋代汴京的雨夜，捡到一卷会改写命运的长卷"} />
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn" disabled={!text.trim() || busy} onClick={save}>保存为本剧素材</button>
            {text && <span className="text-sm text-ink-soft">{text.length.toLocaleString()} 字</span>}
          </div>
        </div>
      )}
      {msg && <p className={msg.ok ? "text-ink" : "text-seal"} role="status">{msg.text}</p>}
      {watch && <p className="text-sm text-dawn" role="status">原片拆解中，完成后自动刷新…</p>}
      {detail.data?.status === "remix_failed" && (
        <p className="text-seal" role="status">
          原片拆解失败。
          {lastVideoPath ? <button className="ml-1 underline" disabled={busy} onClick={() => retryRemix(lastVideoPath)}>重试</button> : "确认是 mp4 或 mov 且带音轨后重新上传。"}
          {lastVideoPath && "；仍失败就换一个文件重新上传。"}
        </p>
      )}
      {detail.data?.has_source_text && (
        <div className="silk border border-celadon-deep/70 p-4">
          <div className="mb-2 flex items-baseline justify-between gap-4">
            <h3 className="font-semibold">当前素材</h3>
            <button className="btn" onClick={() => setTab("script")}>下一步：生成剧本</button>
          </div>
          <p className="line-clamp-5 text-sm leading-7 whitespace-pre-line text-ink-soft">{detail.data.source_novel_text}</p>
        </div>
      )}
    </section>
  );
}
