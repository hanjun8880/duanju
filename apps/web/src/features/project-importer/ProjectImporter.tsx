"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ContentMode, Project } from "@/lib/contracts";
import { useWorkbench } from "@/lib/store";

export function ProjectImporter({ projects, onChanged }: { projects: Project[]; onChanged: () => void }) {
  const { projectId, setProject, setTab } = useWorkbench();
  const current = projects.find((p) => p.id === projectId);
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <NewProject onCreated={(p) => { setProject(p.id); onChanged(); }} />
      {current ? <EpisodeSources project={current} onDone={() => setTab("storyboard")} /> :
        <p className="text-ink-soft">新建项目后，可以在这里粘贴小说章节生成分镜，或上传原片做二创拆解。</p>}
    </div>
  );
}

function NewProject({ onCreated }: { onCreated: (p: Project) => void }) {
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<ContentMode>("standard_sfw");
  const [adult, setAdult] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const create = async () => {
    try {
      const p = await api<Project>("/projects", { method: "POST", json: { title, content_mode: mode, confirm_18plus: adult, source_novel_text: text || null } });
      setTitle(""); setText(""); setErr(null); onCreated(p);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <section aria-labelledby="new-project">
      <h2 id="new-project" className="mb-4 text-xl font-semibold">新建剧集项目</h2>
      <div className="space-y-4">
        <label className="block">剧名<input className="field mt-1" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：雾港来信" /></label>
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
        <label className="block">小说原文或梗概（可选）
          <textarea className="field mt-1 h-40" value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        {err && <p className="text-seal">{err}</p>}
        <button className="btn" disabled={!title || (mode === "uncensored_nsfw" && !adult)} onClick={create}>创建项目</button>
      </div>
    </section>
  );
}

function EpisodeSources({ project, onDone }: { project: Project; onDone: () => void }) {
  const [episode, setEpisode] = useState(1);
  const [chapter, setChapter] = useState("");
  const [json, setJson] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    try { setMsg({ ok: true, text: await fn() }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const compile = () => run(async () => {
    const r = await api<{ created: number; episode_title: string }>(`/projects/${project.id}/storyboard/compile`, { method: "POST", json: { episode_number: episode, text: chapter } });
    onDone(); return `《${r.episode_title}》已拆出 ${r.created} 个镜头`;
  });
  const importJson = () => run(async () => {
    const r = await api<{ created: number }>(`/projects/${project.id}/storyboard`, { method: "POST", json: { episode_number: episode, storyboard: JSON.parse(json) } });
    onDone(); return `已导入 ${r.created} 个镜头`;
  });
  const remix = (file: File) => run(async () => {
    const fd = new FormData(); fd.append("file", file);
    const up = await api<{ path: string }>("/uploads", { method: "POST", body: fd });
    await api(`/projects/${project.id}/remix`, { method: "POST", json: { video_path: up.path, episode_number: episode } });
    return "原片已进入本地拆解队列，切镜与台词完成后会出现在分镜长卷里";
  });
  return (
    <section aria-labelledby="sources" className="space-y-8">
      <div className="flex items-baseline justify-between">
        <h2 id="sources" className="text-xl font-semibold">《{project.title}》分集来源</h2>
        <label className="text-sm">第 <input type="number" min={1} className="field inline w-16 py-1" value={episode} onChange={(e) => setEpisode(Number(e.target.value) || 1)} /> 集</label>
      </div>
      <div>
        <h3 className="mb-1 font-semibold">从小说章节拆镜</h3>
        <p className="mb-2 text-sm text-ink-soft">剧集圣经与规则走一小时提示词缓存，同一部剧后续拆镜只按约一成计费。</p>
        <textarea className="field h-36" value={chapter} onChange={(e) => setChapter(e.target.value)} placeholder="粘贴本集对应的小说章节" />
        <button className="btn mt-2" disabled={!chapter || busy} onClick={compile}>拆成分镜</button>
      </div>
      <div>
        <h3 className="mb-1 font-semibold">导入现成分镜表</h3>
        <textarea className="field h-28 text-sm" value={json} onChange={(e) => setJson(e.target.value)} placeholder='{"episode_title": "...", "hook_summary": "...", "shots": [...]}' />
        <button className="btn btn-quiet mt-2" disabled={!json || busy} onClick={importJson}>导入分镜</button>
      </div>
      <div>
        <h3 className="mb-1 font-semibold">原片二创拆解</h3>
        <p className="mb-2 text-sm text-ink-soft">切镜、转写、关键帧全部在本机完成，不消耗云端额度。</p>
        <label className="btn btn-quiet file-pick">选择原片视频
          <input type="file" accept="video/*" disabled={busy} onChange={(e) => e.target.files?.[0] && remix(e.target.files[0])} />
        </label>
      </div>
      {msg && <p className={msg.ok ? "text-ink" : "text-seal"} role="status">{msg.text}</p>}
    </section>
  );
}
