"use client";
import { useState } from "react";
import { api, lookdevGenerate, lookdevLock, useApi, useBusySet, type LookdevKind } from "@/lib/api";
import type { LookdevAsset as BaseAsset, Project, Template } from "@/lib/contracts";
import { MAX_LOCKED, toggleLock } from "./lock";

// locked_ref_urls is served by the API; contracts.ts is regenerated at merge time.
type LookdevAsset = BaseAsset & { locked_ref_urls?: string[] };

const KIT: { kind: LookdevKind; label: string; types: string[] }[] = [
  { kind: "portrait", label: "定妆", types: ["character", "prop", "scene"] },
  { kind: "turnaround", label: "三视图", types: ["character", "prop"] },
  { kind: "expressions", label: "表情四宫格", types: ["character"] },
  { kind: "costume", label: "换装", types: ["character"] },
];
const USD_PER_IMAGE = 0.03; // display only; server /estimate is authoritative

const KINDS = [
  { type: "character", label: "人物", hint: "出图只是候选；点选最多 3 张锁定，分镜只引用锁定的图。" },
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

  const generate = async (a: LookdevAsset, kind: LookdevKind = "portrait", note?: string): Promise<boolean> => {
    const ok = await run(a.id, async () => {
      try {
        const job = await lookdevGenerate(a.id, kind, note);
        if (job.cached) {
          setMsg({ ok: true, text: `「${a.name}」同样的设定已经出过图，直接复用，未再扣费` });
          return true;
        }
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
        <p className="max-w-2xl text-ink-soft">{`剧集圣经里的人物、道具、场景已自动建卡。每张卡可以出定妆、三视图、表情、换装（每次 1 张，约 $${USD_PER_IMAGE}），也可以上传你自己的图。出的图先进候选，点图锁定（最多 3 张）后分镜才会用。`}</p>
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
                    <AssetCard key={a.id} asset={a} onChanged={assets.reload} busy={pending.has(a.id)} onGenerate={(kind, note) => generate(a, kind, note)} />
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

function AssetCard({ asset: a, onChanged, onGenerate, busy }: { asset: LookdevAsset; onChanged: () => void; onGenerate: (kind: LookdevKind, note?: string) => void; busy: boolean }) {
  const [err, setErr] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const patch = async (json: Record<string, unknown>) => {
    try { await api(`/lookdev/${a.id}`, { method: "PATCH", json }); setErr(null); onChanged(); } catch (e) { setErr((e as Error).message); }
  };
  const locked = a.locked_ref_urls ?? [];
  const [note, setNote] = useState("");
  const toggle = async (u: string) => {
    const next = toggleLock(locked, u);
    if (next === null) { setErr(`最多锁定 ${MAX_LOCKED} 张，先点掉一张`); return; }
    try { await lookdevLock(a.id, next); setErr(null); onChanged(); } catch (e) { setErr((e as Error).message); }
  };
  const kit = (kind: LookdevKind) => {
    if (kind === "costume" && !note.trim()) { setErr("换装先在下面写新服装"); return; }
    onGenerate(kind, note.trim() || undefined);
  };
  const drop = (u: string) => patch({ ref_urls: a.ref_urls.filter((x) => x !== u) });
  const add = async (files: FileList) => {
    try { patch({ ref_urls: [...a.ref_urls, ...(await uploadAll(files))].slice(0, 12), likeness_consent: consent || undefined }); } catch (e) { setErr((e as Error).message); }
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
        <>
          <p className="mt-2 text-xs text-ink-soft">{locked.length ? `已锁定 ${locked.length}/${MAX_LOCKED}，分镜只用这几张` : "未锁定：点图锁定，分镜才有参考"}</p>
          <div className="mt-1 grid grid-cols-3 gap-2">
            {a.ref_urls.map((u, i) => {
              const on = locked.includes(u);
              return (
                <figure key={u}>
                  <button type="button" className="block w-full" aria-pressed={on} onClick={() => toggle(u)}
                    aria-label={`${on ? "解锁" : "锁定"} ${a.name} 候选 ${i + 1}`}>
                    <img src={u} alt={`${a.name} 候选 ${i + 1}`} className={`aspect-square w-full object-cover ${on ? "border-2 border-ink" : "border border-celadon-deep opacity-80"}`} />
                  </button>
                  <figcaption className="mt-1 flex justify-between text-xs">
                    <span className={on ? "text-ink" : "text-ink-soft"}>{on ? `锁定 ${locked.indexOf(u) + 1}` : "候选"}</span>
                    <button className="text-ink-soft underline" onClick={() => drop(u)}>移除</button>
                  </figcaption>
                </figure>
              );
            })}
          </div>
        </>
      ) : <p className="mt-2 text-sm text-ink-soft">还没有参考图。</p>}
      {a.asset_type === "character" && (
        <label className="mt-2 flex items-start gap-2 text-xs text-ink-soft">
          <input type="checkbox" className="mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          上传的是虚构合成角色，或我已取得完整肖像授权
        </label>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {KIT.filter((k) => k.types.includes(a.asset_type)).map((k) => (
          <button key={k.kind} className="btn btn-quiet" disabled={busy} onClick={() => kit(k.kind)}
            title={`1 张，约 $${USD_PER_IMAGE}；${locked.length ? "以锁定图为参考" : "未锁定，纯文字出图"}`}>{busy ? "提交中…" : k.label}</button>
        ))}
        <label className={`btn btn-quiet file-pick ${a.asset_type === "character" && !consent ? "opacity-45" : ""}`}>上传
          <input type="file" accept="image/*" multiple disabled={a.asset_type === "character" && !consent} onChange={(e) => e.target.files && add(e.target.files)} />
        </label>
      </div>
      {a.asset_type === "character" && (
        <input className="field mt-2 text-sm" placeholder="补充 / 换装的新服装（可选）" aria-label={`${a.name} 出图补充`}
          value={note} onChange={(e) => setNote(e.target.value)} />
      )}
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
