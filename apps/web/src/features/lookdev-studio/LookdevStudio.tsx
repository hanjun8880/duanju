"use client";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import type { Job, LookdevAsset, Project, Template } from "@/lib/contracts";

const KINDS = [
  { type: "character", label: "人物", hint: "正面、半身、全身三到五张，锁定面部与服装" },
  { type: "prop", label: "道具", hint: "白底多角度，或带比例的手持参考" },
  { type: "scene", label: "场景", hint: "宽幅空镜，锁定透视、陈设与色调" },
] as const;

export function LookdevStudio({ project }: { project: Project }) {
  const assets = useApi<LookdevAsset[]>(`/projects/${project.id}/lookdev`);
  const templates = useApi<Template[]>(`/projects/${project.id}/templates`);
  return (
    <div className="space-y-12">
      <div className="grid gap-8 lg:grid-cols-3">
        {KINDS.map((k) => (
          <AssetColumn key={k.type} project={project} kind={k} assets={(assets.data ?? []).filter((a) => a.asset_type === k.type)} onChanged={assets.reload} />
        ))}
      </div>
      <section aria-labelledby="tpl">
        <h2 id="tpl" className="text-xl font-semibold">动作与构图模板</h2>
        <p className="mb-4 text-sm text-ink-soft">直接读库，不调用模型。分镜按姿态名自动匹配。</p>
        <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
          {templates.data?.map((t) => (
            <div key={t.id} className="border-l-2 border-celadon-deep pl-3">
              <div className="font-semibold">{t.pose}<span className="ml-2 text-sm font-normal text-ink-soft">{t.scene}，{t.recommended_duration} 秒</span></div>
              <p className="text-sm text-ink-soft">{t.default_prompt}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function AssetColumn({ project, kind, assets, onChanged }: {
  project: Project; kind: (typeof KINDS)[number]; assets: LookdevAsset[]; onChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [urls, setUrls] = useState<string[]>([]);
  const [consent, setConsent] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const upload = async (files: FileList) => {
    const out: string[] = [];
    for (const f of Array.from(files)) {
      const fd = new FormData(); fd.append("file", f);
      out.push((await api<{ url: string }>("/uploads", { method: "POST", body: fd })).url);
    }
    setUrls((u) => [...u, ...out]);
  };
  const save = async () => {
    try {
      await api(`/projects/${project.id}/lookdev`, { method: "POST", json: { asset_type: kind.type, name, visual_prompt: prompt, ref_urls: urls, likeness_consent: consent } });
      setName(""); setPrompt(""); setUrls([]); setConsent(false); setMsg(null); onChanged();
    } catch (e) { setMsg((e as Error).message); }
  };
  const generate = async () => {
    try {
      const job = await api<Job>("/jobs", { method: "POST", json: { project_id: project.id, task_type: "image.t2i", model_key: "flux_uncensored", prompt: `${name}，${prompt}` } });
      setMsg(`抽卡任务已排队（预扣 ${Number(job.credit_estimate).toFixed(1)} 积分），完成后在账本里查看结果`);
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <section aria-labelledby={`k-${kind.type}`}>
      <h2 id={`k-${kind.type}`} className="text-xl font-semibold">{kind.label}</h2>
      <p className="mb-4 text-sm text-ink-soft">{kind.hint}</p>
      <ul className="mb-6 space-y-4">
        {assets.length === 0 && <li className="text-sm text-ink-soft">还没有{kind.label}定妆。</li>}
        {assets.map((a) => (
          <li key={a.id}>
            <div className="font-semibold">{a.name}</div>
            <p className="text-sm text-ink-soft">{a.visual_prompt}</p>
            {a.ref_urls.length > 0 && (
              <div className="mt-2 flex gap-1 overflow-x-auto">
                {a.ref_urls.map((u) => <img key={u} src={u} alt={`${a.name} 定妆参考`} className="h-20 w-auto border border-celadon-deep object-cover" />)}
              </div>
            )}
          </li>
        ))}
      </ul>
      <div className="silk space-y-3 border border-celadon-deep/70 p-4">
        <label className="block text-sm">名称<input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="block text-sm">外观设定<textarea className="field mt-1 h-20" value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
        <div className="flex items-center gap-3 text-sm">
          <label className="btn btn-quiet file-pick">上传参考图
            <input type="file" accept="image/*" multiple onChange={(e) => e.target.files && upload(e.target.files)} />
          </label>
          <span className="text-ink-soft">{urls.length ? `已上传 ${urls.length} 张` : "可多选"}</span>
        </div>
        {kind.type === "character" && (
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            这是虚构合成角色，或我已取得完整肖像授权
          </label>
        )}
        {msg && <p className="text-sm text-seal" role="status">{msg}</p>}
        <div className="flex gap-2">
          <button className="btn" disabled={!name || !prompt} onClick={save}>锁定定妆</button>
          <button className="btn btn-quiet" disabled={!name || !prompt} onClick={generate}>文生图抽卡</button>
        </div>
      </div>
    </section>
  );
}
