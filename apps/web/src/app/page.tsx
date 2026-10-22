"use client";
import { useEffect } from "react";
import type { Me, Project } from "@/lib/contracts";
import { useApi } from "@/lib/api";
import { useWorkbench, type Tab } from "@/lib/store";
import { ProjectImporter } from "@/features/project-importer/ProjectImporter";
import { LookdevStudio } from "@/features/lookdev-studio/LookdevStudio";
import { StoryboardCanvas } from "@/features/storyboard-canvas/StoryboardCanvas";
import { AdminLedger } from "@/features/admin-ledger/AdminLedger";

const TABS: { id: Tab; label: string }[] = [
  { id: "import", label: "剧本与导入" },
  { id: "lookdev", label: "定妆" },
  { id: "storyboard", label: "分镜长卷" },
];

export default function Workbench() {
  const { projectId, setProject, tab, setTab, ledgerOpen, setLedgerOpen } = useWorkbench();
  const projects = useApi<Project[]>("/projects");
  const me = useApi<Me>("/me");
  const current = projects.data?.find((p) => p.id === projectId) ?? null;

  useEffect(() => {
    if (!projectId && projects.data?.length) setProject(projects.data[0].id);
  }, [projectId, projects.data, setProject]);

  return (
    <div className="min-h-screen">
      <header className="border-b border-celadon-deep/60">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-end gap-x-8 gap-y-3 px-4 pt-5 pb-3 sm:px-8">
          <h1 className="text-3xl font-black tracking-[0.3em] text-ink">长卷</h1>
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <label htmlFor="project" className="sr-only">当前剧集项目</label>
            <select id="project" className="field max-w-xs" value={projectId ?? ""} onChange={(e) => setProject(e.target.value || null)}>
              {!projects.data?.length && <option value="">还没有项目</option>}
              {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
            </select>
            {current?.content_mode === "uncensored_nsfw" && (
              <span className="border border-seal px-2 py-0.5 text-sm text-seal" title="已确认 18+，仅限虚构成年角色">十八禁</span>
            )}
          </div>
          <div className="flex items-center gap-4 text-sm text-ink-soft">
            {me.data && <span>余额 <b className="text-lg text-ink">{Number(me.data.credits).toFixed(1)}</b> 积分，日限 {Number(me.data.daily_limit_credits).toFixed(0)}</span>}
            <button className="btn btn-quiet" onClick={() => setLedgerOpen(true)}>账本与模型</button>
          </div>
        </div>
        <nav className="mx-auto flex max-w-[1400px] gap-6 px-4 sm:px-8" aria-label="工作区">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} aria-current={tab === t.id ? "page" : undefined}
              className={`-mb-px border-b-2 pb-2 text-base ${tab === t.id ? "border-ink font-semibold text-ink" : "border-transparent text-ink-soft hover:text-ink"}`}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-8">
        {projects.error && <p className="mb-4 text-seal">后端未连接：{projects.error}。请先启动 apps/server（uvicorn src.main:app）。</p>}
        {tab === "import" && <ProjectImporter projects={projects.data ?? []} onChanged={() => { projects.reload(); me.reload(); }} />}
        {tab === "lookdev" && (current ? <LookdevStudio project={current} /> : <NeedProject />)}
        {tab === "storyboard" && (current ? <StoryboardCanvas project={current} onSpent={me.reload} /> : <NeedProject />)}
      </main>

      {ledgerOpen && <AdminLedger onClose={() => { setLedgerOpen(false); me.reload(); }} />}
    </div>
  );
}

function NeedProject() {
  return <p className="text-ink-soft">先在「剧本与导入」里新建一个剧集项目。</p>;
}
