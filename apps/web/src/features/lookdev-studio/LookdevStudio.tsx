"use client";
import { useState } from "react";
import { api, useApi, useBusySet } from "@/lib/api";
import type { Job, LookdevAsset, Project, Template } from "@/lib/contracts";

const KINDS = [
  { type: "character", label: "人物", hint: "锁定面部、发型与服装。分镜里每个出场人物都引用这里的首图。" },
  { type: "prop", label: "道具", hint: "单件居中、材质清晰，多镜头里保持同一件。" },
  { type: "scene", label: "场景", hint: "空镜，锁定透视、陈设与色调。" },
] as const;

async function uploadAll(files: FileList): Promise<string[]> {
  const out: string[] = [];
  for (const f of Array.from(files)) {
    const fd = new FormData(); fd.append("file", f);
    out.push((await api<{ url: string }>("/uploads", { method: "POST", body: fd })).url);
  }
  return out;
}

export function LookdevStudio({ project, onSpent }: { project: Project; onSpent: () => void }) {
  const assets = useApi<LookdevAsset[]>(`/projects/${project.id}/lookdev`, 5000);
  const templates = useApi<Template[]>(`/projects/${project.id}/templates`);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [generatingAll, setGeneratingAll] = useState(false);
  const { busy: pending, run } = useBusySet<string>();
  const list = assets.data ?? [];
  const missing = list.filter((a) => a.ref_urls.length === 0);

  const generate = async (a: LookdevAsset): Promise<boolean> => {
    const ok = await run(a.id, async () => {
      try {
        const job = await api<Job>(`/lookdev/${a.id}/generate`, { method: "POST" });
        onSpent();
        setMsg({ ok: true, text: `「${a.name}」出图已提交，预扣 ${Number(job.credit_estimate).toFixed(1)} 积分` });
        return true;
      } catch (e) {
        setMsg({ ok: false, text: `「${a.name}」：${(e as Error).message}` });
        return false;
      }
    });
    return ok ?? false; // undefined means run() rejected a double click; that's not a failure to count
  };
  const generateMissing = async () => {
    setGeneratingAll(true);
    let ok = 0;
    for (const a of missing) {
      if (await generate(a)) ok++;
    }
    setGeneratingAll(false);
    setMsg({ ok: ok === missing.length, text: `已为 ${ok}/${missing.length} 张缺图的卡提交出图，完成后自动出现在卡片上` });
  };

  return (
    <div className="space-y-12">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <p className="max-w-2xl text-ink-soft">剧集圣经里的人物、道具、场景已自动建卡。每张卡可以让模型出参考图，也可以上传你自己的图；排在第一位的是首图。</p>
        {missing.length > 0 && <button className="btn" disabled={generatingAll} onClick={generateMissing}>{generatingAll ? "出图提交中…" : `为 ${missing.length} 张缺图的卡出参考图`}</button>}
      </div>
      {msg && <p className={msg.ok ? "text-ink" : "text-seal"} role="status">{msg.text}</p>}
      {assets.loading ? <p className="text-ink-soft">定妆卡加载中…</p> : (
        <div className="grid gap-10 lg:grid-cols-3">
          {KINDS.map((k) => {
            const cards = list.filter((a) => a.asset_type === k.type);
            return (
              <section key={k.type} aria-labelledby={`k-${k.type}`}>
                <h2 id={`k-${k.type}`} className="text-xl font-semibold">{k.label}</h2>
                <p className="mb-4 text-sm text-ink-soft">{k.hint}</p>
                {cards.length === 0 && <p className="mb-4 text-sm text-ink-soft">还没有{k.label}卡。先在「剧本」里生成剧集圣经，或在下面手动添加。</p>}
                <ul className="mb-6 space-y-6">
                  {cards.map((a) => (
                    <AssetCard key={a.id} asset={a} onChanged={assets.reload} busy={pending.has(a.id)} onGenerate={() => generate(a)} />
                  ))}
                </ul>
                <NewAsset project={project} kind={k} onCreated={assets.reload} />
              </section>
            );
          })}
        </div>
      )}
      <section aria-labelledby="tpl">
        <h2 id="tpl" className="text-xl font-semibold">动作与构图模板</h2>
        <p className="mb-4 text-sm text-ink-soft">直接读库，不调用模型。分镜按姿态名自动匹配。</p>
        {templates.error ? <p className="text-seal">模板加载失败：{templates.error}</p> : (
          <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
            {templates.data?.map((t) => (
              <div key={t.id} className="border-l-2 border-celadon-deep pl-3">
                <div className="font-semibold">{t.pose}<span className="ml-2 text-sm font-normal text-ink-soft">{t.scene}，{t.recommended_duration} 秒</span></div>
                <p className="text-sm text-ink-soft">{t.default_prompt}</p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function AssetCard({ asset: a, onChanged, onGenerate, busy }: { asset: LookdevAsset; onChanged: () => void; onGenerate: () => void; busy: boolean }) {
  const [err, setErr] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const patch = async (json: Record<string, unknown>) => {
    try { await api(`/lookdev/${a.id}`, { method: "PATCH", json }); setErr(null); onChanged(); } catch (e) { setErr((e as Error).message); }
  };
  const move = (u: string) => patch({ ref_urls: [u, ...a.ref_urls.filter((x) => x !== u)] });
  const drop = (u: string) => patch({ ref_urls: a.ref_urls.filter((x) => x !== u) });
  const add = async (files: FileList) => {
    try { patch({ ref_urls: [...a.ref_urls, ...(await uploadAll(files))].slice(0, 10), likeness_consent: consent || undefined }); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <li className="border-t border-celadon-deep/70 pt-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-semibold">{a.name}</span>
        {a.role_tag && <span className="text-sm text-ink-soft">{a.role_tag}</span>}
      </div>
      <textarea className="field mt-2 h-20 text-sm leading-6" defaultValue={a.visual_prompt} aria-label={`${a.name} 外观设定`}
        onBlur={(e) => e.target.value !== a.visual_prompt && e.target.value.trim() && patch({ visual_prompt: e.target.value })} />
      {a.ref_urls.length > 0 ? (
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {a.ref_urls.map((u, i) => (
            <figure key={u} className="shrink-0">
              <img src={u} alt={`${a.name} 参考图 ${i + 1}`} className={`h-28 w-auto border object-cover ${i === 0 ? "border-ink" : "border-celadon-deep"}`} />
              <figcaption className="mt-1 flex gap-2 text-xs">
                {i === 0 ? <span className="text-ink">首图</span> : <button className="text-ink-soft underline" onClick={() => move(u)}>设为首图</button>}
                <button className="text-ink-soft underline" onClick={() => drop(u)}>移除</button>
              </figcaption>
            </figure>
          ))}
        </div>
      ) : <p className="mt-2 text-sm text-ink-soft">还没有参考图。</p>}
      {a.asset_type === "character" && (
        <label className="mt-2 flex items-start gap-2 text-xs text-ink-soft">
          <input type="checkbox" className="mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          上传的是虚构合成角色，或我已取得完整肖像授权
        </label>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        <button className="btn btn-quiet" disabled={busy} onClick={onGenerate}>{busy ? "出图提交中…" : a.ref_urls.length ? "再出一张" : "出参考图"}</button>
        <label className={`btn btn-quiet file-pick ${a.asset_type === "character" && !consent ? "opacity-45" : ""}`}>上传
          <input type="file" accept="image/*" multiple disabled={a.asset_type === "character" && !consent} onChange={(e) => e.target.files && add(e.target.files)} />
        </label>
      </div>
      {err && <p className="mt-1 text-sm text-seal" role="status">{err}</p>}
    </li>
  );
}

function NewAsset({ project, kind, onCreated }: { project: Project; kind: (typeof KINDS)[number]; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    try {
      await api(`/projects/${project.id}/lookdev`, { method: "POST", json: { asset_type: kind.type, name, visual_prompt: prompt } });
      setName(""); setPrompt(""); setErr(null); onCreated();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <details className="silk border border-celadon-deep/70 p-4">
      <summary className="cursor-pointer text-sm">手动添加{kind.label}</summary>
      <div className="mt-3 space-y-3">
        <label className="block text-sm">名称<input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="block text-sm">外观设定<textarea className="field mt-1 h-20" value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
        {err && <p className="text-sm text-seal">{err}</p>}
        <button className="btn" disabled={!name || !prompt} onClick={save}>添加</button>
      </div>
    </details>
  );
}
