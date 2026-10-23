"use client";
import { useEffect, useRef, useState } from "react";
import { api, remixApi, useApi, useBusySet, type RemixEstimate, type RemixMode, type RemixSource } from "@/lib/api";
import type { Project } from "@/lib/contracts";
import { MODES, STATUS_LABEL, allReady, buildSpec, isWorking, validateSpec, type SpecForm } from "./remix";

/** 二创工作室: external footage (multi-episode) -> new series, or fork one of your own series. */
export function RemixStudio({ projects, currentId, onClose, onCreated }: {
  projects: Project[]; currentId: string | null; onClose: () => void; onCreated: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<"external" | "fork">("external");
  useEffect(() => {
    ref.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-ink/30" onClick={onClose}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="remix-title"
        className="h-full w-full max-w-3xl overflow-y-auto bg-paper px-6 py-6 shadow-[-12px_0_32px_rgb(34_48_47/0.18)]" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-baseline justify-between">
          <h2 id="remix-title" className="text-2xl font-semibold">二创工作室</h2>
          <button className="btn btn-quiet" onClick={onClose}>关闭</button>
        </div>
        <div className="mb-6 flex gap-6 border-b border-celadon" role="tablist">
          {([["external", "外部原片 → 新剧"], ["fork", "Fork 自家项目"]] as const).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={`-mb-px border-b-2 pb-2 ${tab === id ? "border-ink font-semibold" : "border-transparent text-ink-soft"}`}>{label}</button>
          ))}
        </div>
        {tab === "external"
          ? <External projects={projects} currentId={currentId} onCreated={onCreated} />
          : <Fork projects={projects} currentId={currentId} onCreated={onCreated} />}
      </div>
    </div>
  );
}

function External({ projects, currentId, onCreated }: { projects: Project[]; currentId: string | null; onCreated: (id: string) => void }) {
  const remixProjects = projects.filter((p) => p.category === "remix");
  const [pid, setPid] = useState<string>(remixProjects.find((p) => p.id === currentId)?.id ?? remixProjects[0]?.id ?? "");
  const [poll, setPoll] = useState(false);
  const sources = useApi<RemixSource[]>(pid ? `/remix/projects/${pid}/sources` : null, poll ? 2000 : undefined);
  const [estimate, setEstimate] = useState<RemixEstimate | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [ep, setEp] = useState(1);
  const [form, setForm] = useState<SpecForm>({ modes: [] });
  const [title, setTitle] = useState("");
  const { busy, run } = useBusySet<string>();
  const list = sources.data ?? [];

  useEffect(() => { setPoll(isWorking(list)); }, [list]);
  useEffect(() => { setEp(list.length ? Math.max(...list.map((s) => s.episode_number)) + 1 : 1); }, [list.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = (key: string, fn: () => Promise<string | void>) => run(key, async () => {
    try { const m = await fn(); setMsg(m || null); } catch (e) { setMsg((e as Error).message); }
    sources.reload();
  });

  const newSource = async () => {
    const title = window.prompt("原片项目名称", "原片二创");
    if (!title) return;
    const p = await api<Project>("/projects", { method: "POST", json: { title, category: "remix" } });
    setPid(p.id); onCreated(p.id);
  };

  const spec = buildSpec(form);
  const specErr = validateSpec(spec);

  return (
    <section className="space-y-5">
      {msg && <p role="status" className="text-ink-soft">{msg}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="rx-src">原片项目</label>
        <select id="rx-src" className="field max-w-xs" value={pid} onChange={(e) => { setPid(e.target.value); setEstimate(null); }}>
          {!remixProjects.length && <option value="">还没有二创项目</option>}
          {remixProjects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
        </select>
        <button className="btn btn-quiet" onClick={() => act("new", newSource)}>新建</button>
      </div>

      {pid && <>
        <div>
          <h3 className="mb-2 font-semibold">一、逐集登记原片</h3>
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="rx-ep">第</label>
            <input id="rx-ep" type="number" min={1} className="field w-20" value={ep} onChange={(e) => setEp(Number(e.target.value) || 1)} />
            <span>集</span>
            <input aria-label="选择原片视频" type="file" accept="video/mp4,video/quicktime" disabled={busy.has("up")}
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) act("up", async () => {
                const fd = new FormData(); fd.append("file", f);
                const up = await api<{ path: string }>("/uploads", { method: "POST", body: fd });
                const s = await remixApi.register(pid, ep, up.path);
                return s.cache_hit ? `第${ep}集已登记：同一文件拆解过，直接复用缓存。` : `第${ep}集已登记。`;
              }); }} />
          </div>
          <table className="mt-3 w-full text-sm">
            <thead><tr className="text-left text-ink-soft"><th className="py-1 font-normal">集</th><th className="font-normal">状态</th><th className="font-normal">镜头 / 去重后</th><th className="font-normal" /></tr></thead>
            <tbody>
              {list.map((s) => (
                <tr key={s.episode_number} className="border-t border-celadon">
                  <td className="py-1.5">第{s.episode_number}集{s.cache_hit && <span className="ml-1 text-xs text-ink-soft">缓存</span>}</td>
                  <td>{STATUS_LABEL[s.status] ?? s.status}{isWorking([s]) && ` ${Math.round(s.progress * 100)}%`}
                    {s.error && <div className="text-xs text-seal">{s.error}</div>}</td>
                  <td>{s.shots ?? "—"} / {s.kept ?? "—"}</td>
                  <td className="text-right"><button className="btn btn-quiet py-0.5" disabled={isWorking([s])}
                    onClick={() => act(`rm${s.episode_number}`, async () => { await remixApi.remove(pid, s.episode_number); })}>移除</button></td>
                </tr>
              ))}
              {!list.length && !sources.loading && <tr><td colSpan={4} className="py-2 text-ink-soft">还没有登记任何一集。</td></tr>}
            </tbody>
          </table>
        </div>

        <div>
          <h3 className="mb-2 font-semibold">二、拆解与识别</h3>
          <div className="flex flex-wrap gap-3">
            <button className="btn" disabled={!list.length || isWorking(list) || busy.has("dec")}
              onClick={() => act("dec", async () => { await remixApi.deconstruct(pid, false); return "已进入本地拆解（切镜 + 转写 + 去重，零 token）。"; })}>本地拆解（零 token）</button>
            <button className="btn btn-quiet" disabled={!allReady(list) || busy.has("est")}
              onClick={() => act("est", async () => { setEstimate(await remixApi.estimate(pid)); })}>预估识别费用</button>
            <button className="btn btn-quiet" disabled={!estimate || isWorking(list) || busy.has("desc")}
              onClick={() => act("desc", async () => { await remixApi.deconstruct(pid, true); return "已提交画面识别。"; })}>
              识别画面{estimate?.total.usd != null ? `（约 $${estimate.total.usd.toFixed(3)}）` : ""}</button>
          </div>
          {estimate && (
            <p className="mt-2 text-sm text-ink-soft">
              {estimate.model}：送 {estimate.total.images} 张图、{estimate.total.batches} 批；
              {estimate.episodes.map((e) => `第${e.episode_number}集 ${e.images_without_dedupe ?? "?"}→${e.images ?? "?"}`).join("，")}。
              单价为第三方参考价，未核实。
            </p>
          )}
        </div>

        <div>
          <h3 className="mb-2 font-semibold">三、二创方式（可组合）</h3>
          <SpecEditor form={form} setForm={setForm} />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <input aria-label="新项目名称" className="field max-w-xs" placeholder="新项目名称（可选）" value={title} onChange={(e) => setTitle(e.target.value)} />
            <button className="btn" disabled={!allReady(list) || !!specErr || busy.has("gen")} title={specErr ?? undefined}
              onClick={() => act("gen", async () => { const p = await remixApi.generate(pid, spec, title); onCreated(p.id); return `已生成新项目「${p.title}」。外部原片人物需在「定妆」里重新定妆。`; })}>生成新项目</button>
            {specErr && form.modes.length > 0 && <span className="text-sm text-seal">{specErr}</span>}
          </div>
        </div>
      </>}
    </section>
  );
}

function Fork({ projects, currentId, onCreated }: { projects: Project[]; currentId: string | null; onCreated: (id: string) => void }) {
  const [pid, setPid] = useState(currentId ?? projects[0]?.id ?? "");
  const [title, setTitle] = useState("");
  const [withSpec, setWithSpec] = useState(false);
  const [form, setForm] = useState<SpecForm>({ modes: [] });
  const [msg, setMsg] = useState<string | null>(null);
  const { busy, run } = useBusySet<string>();
  const spec = buildSpec(form);
  const specErr = withSpec ? validateSpec(spec) : null;
  return (
    <section className="space-y-4">
      <p className="text-sm text-ink-soft">继承剧集圣经、分集、已锁定的定妆与配音、空镜，不重新生成；可选再叠加二创方式。</p>
      {msg && <p role="status" className="text-ink-soft">{msg}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="fk-src">来源项目</label>
        <select id="fk-src" className="field max-w-xs" value={pid} onChange={(e) => setPid(e.target.value)}>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
        </select>
        <input aria-label="新项目名称" className="field max-w-xs" placeholder="新项目名称（可选）" value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <label className="flex items-center gap-2"><input type="checkbox" checked={withSpec} onChange={(e) => setWithSpec(e.target.checked)} />同时二创（调用模型改写大纲）</label>
      {withSpec && <SpecEditor form={form} setForm={setForm} />}
      <button className="btn" disabled={!pid || !!specErr || busy.has("fork")} title={specErr ?? undefined}
        onClick={() => run("fork", async () => {
          try { const p = await remixApi.fork(pid, title, withSpec ? spec : undefined); onCreated(p.id); setMsg(`已创建「${p.title}」，共 ${p.episodes ?? 0} 集。`); }
          catch (e) { setMsg((e as Error).message); }
        })}>一键 Fork</button>
      {specErr && form.modes.length > 0 && <span className="ml-3 text-sm text-seal">{specErr}</span>}
    </section>
  );
}

function SpecEditor({ form, setForm }: { form: SpecForm; setForm: (f: SpecForm) => void }) {
  const toggle = (m: RemixMode) => setForm({ ...form, modes: form.modes.includes(m) ? form.modes.filter((x) => x !== m) : [...form.modes, m] });
  const has = (m: RemixMode) => form.modes.includes(m);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {MODES.map((m) => (
          <button key={m.id} type="button" aria-pressed={has(m.id)} title={m.hint} onClick={() => toggle(m.id)}
            className={`border px-3 py-1 text-sm ${has(m.id) ? "border-ink bg-ink text-paper" : "border-celadon-deep"}`}>{m.label}</button>
        ))}
      </div>
      {has("recast") && <textarea aria-label="换人物" className="field w-full" rows={2} placeholder="林晚=改成男性律师（每行一个；留空=全部重新设计）"
        value={form.recastText ?? ""} onChange={(e) => setForm({ ...form, recastText: e.target.value })} />}
      {has("restyle") && <input aria-label="目标风格" className="field w-full" placeholder="目标风格，如：民国" value={form.style ?? ""} onChange={(e) => setForm({ ...form, style: e.target.value })} />}
      {has("replot") && <input aria-label="剧情方向" className="field w-full" placeholder="剧情改动方向" value={form.plot_direction ?? ""} onChange={(e) => setForm({ ...form, plot_direction: e.target.value })} />}
      {has("spinoff") && <input aria-label="番外焦点" className="field w-full" placeholder="番外围绕的人物或支线" value={form.spinoff_focus ?? ""} onChange={(e) => setForm({ ...form, spinoff_focus: e.target.value })} />}
      {has("condense") && <input aria-label="精简比例" type="number" step={0.1} min={0.1} max={0.9} className="field w-28" placeholder="0.5"
        value={form.condense_ratio ?? ""} onChange={(e) => setForm({ ...form, condense_ratio: e.target.value ? Number(e.target.value) : null })} />}
      <input aria-label="集数" type="number" min={1} max={200} className="field w-28" placeholder="集数（可选）"
        value={form.episode_count ?? ""} onChange={(e) => setForm({ ...form, episode_count: e.target.value ? Number(e.target.value) : null })} />
    </div>
  );
}
