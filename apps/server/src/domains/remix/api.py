"""Remix studio API: multi-episode registration, local deconstruct progress, vision estimate, generate a new
project from external footage, fork an own project, clear a redesign flag after fresh look-dev."""
import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.config import settings
from src.deps import Session
from src.domains.gateway.routing import model_for
from src.domains.gateway.safety import ContentBlocked
from src.domains.remix import compliance, dedupe, fork, modes, rhythm, tasks
from src.domains.remix.modes import RemixSpec
from src.domains.script import series, usage
from src.domains.script.api import project_view
from src.domains.script.script_engine import CachedDirectorLLM, LLMRefused, SeriesPlan
from src.domains.storyboard import shot_orchestrator as orch
from src.models import Episode, LookdevAsset, Project

api = APIRouter(prefix="/api/v1/remix", tags=["remix"])


def make_llm(model: str) -> CachedDirectorLLM:
    return CachedDirectorLLM(model=model)


async def _project(session: Session, project_id: uuid.UUID) -> Project:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


def _need_claude(content_mode: str) -> None:
    if content_mode == "standard_sfw" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured on the server")


def _source_view(ep: str, e: dict[str, Any]) -> dict[str, Any]:
    return {"episode_number": int(ep), "status": e.get("status"), "progress": e.get("progress", 0.0),
            "sha256": e.get("sha256"), "cache_hit": e.get("cache_hit", False), "shots": e.get("shots"),
            "kept": e.get("kept"), "error": e.get("error"), "video_path": e.get("video_path")}


def _sources(project_id: uuid.UUID) -> list[dict[str, Any]]:
    eps = tasks.read_manifest(str(project_id))["episodes"]
    return [_source_view(k, eps[k]) for k in sorted(eps, key=int)]


class SourceRegister(BaseModel):
    episode_number: int = Field(ge=1, le=500)
    video_path: str


@api.post("/projects/{project_id}/sources", status_code=201)
async def register_source(project_id: uuid.UUID, body: SourceRegister, session: Session) -> dict[str, Any]:
    await _project(session, project_id)
    root = Path(settings.storage_dir).resolve()
    src = Path(body.video_path).resolve()
    if root not in src.parents or not src.is_file():  # only files we stored ourselves
        raise HTTPException(422, "video_path must be a file returned by /uploads")
    sha = await asyncio.to_thread(dedupe.file_sha256, str(src))
    with tasks.manifest(str(project_id)) as m:  # re-registering an episode replaces it
        m["episodes"][str(body.episode_number)] = {"video_path": str(src), "sha256": sha, "status": "registered",
                                                   "progress": 0.0, "cache_hit": tasks.cache().get(sha) is not None}
    return _source_view(str(body.episode_number), tasks.read_manifest(str(project_id))["episodes"][
        str(body.episode_number)])


@api.get("/projects/{project_id}/sources")
async def list_sources(project_id: uuid.UUID, session: Session) -> list[dict[str, Any]]:
    await _project(session, project_id)
    return _sources(project_id)


@api.delete("/projects/{project_id}/sources/{ep}", status_code=204)
async def delete_source(project_id: uuid.UUID, ep: int, session: Session) -> Response:
    await _project(session, project_id)
    with tasks.manifest(str(project_id)) as m:
        if m["episodes"].pop(str(ep), None) is None:
            raise HTTPException(404, f"episode {ep} not registered")
    return Response(status_code=204)


class DeconstructRequest(BaseModel):
    episodes: list[int] | None = Field(default=None, description="默认全部已登记的集")
    describe: bool = Field(default=False, description="拆解后调用视觉模型识别关键帧（花钱，先看 /estimate）")


@api.post("/projects/{project_id}/deconstruct", status_code=202)
async def deconstruct(project_id: uuid.UUID, body: DeconstructRequest, session: Session,
                      background: BackgroundTasks) -> dict[str, Any]:
    p = await _project(session, project_id)
    if body.describe:
        if p.content_mode != "standard_sfw":
            raise HTTPException(422, "keyframe vision runs on Claude; the private uncensored LLM is text-only")
        _need_claude(p.content_mode)
    eps = tasks.read_manifest(str(project_id))["episodes"]
    todo = sorted(int(k) for k in eps) if body.episodes is None else sorted(set(body.episodes))
    missing = [e for e in todo if str(e) not in eps]
    if missing or not todo:
        raise HTTPException(422, f"episodes not registered: {missing or 'none'}")
    for ep in todo:
        if eps[str(ep)].get("status") not in ("deconstructing", "describing"):
            tasks.update_episode(str(project_id), ep, status="queued", error=None)
            if settings.dispatch_mode == "inline":
                background.add_task(tasks.run_episode, str(project_id), ep, body.describe)
            else:
                orch.enqueue_local("remix.episode", str(project_id), ep, body.describe)
    return {"queue": "local_post", "queued": todo}


@api.get("/projects/{project_id}/estimate")
async def estimate(project_id: uuid.UUID, session: Session) -> dict[str, Any]:
    """Vision cost for the deconstructed episodes: deduped frames, frames already described (cache) and frames
    shared with an earlier episode (same sha256) cost nothing."""
    await _project(session, project_id)
    model = model_for("remix_vision").model
    c, seen, rows = tasks.cache(), set(), []
    all_paths: list[str] = []
    for ep, e in sorted(tasks.read_manifest(str(project_id))["episodes"].items(), key=lambda kv: int(kv[0])):
        if e.get("kept_orders") is None:
            rows.append({"episode_number": int(ep), "status": e.get("status"), "images": None, "usd": None})
            continue
        shots = tasks.episode_shots(e)
        free = e["sha256"] in seen or c.get_notes(e["sha256"], model) is not None
        seen.add(e["sha256"])
        keep = set(e["kept_orders"])
        paths = [] if free else [s["keyframe_path"] for s in shots if s["shot_order"] in keep]
        est = await asyncio.to_thread(dedupe.estimate_vision, paths, model)
        raw = 0 if free else len(shots)
        all_paths += paths
        rows.append({"episode_number": int(ep), "status": e.get("status"), "shots": len(shots),
                     "images_without_dedupe": raw, **{k: est[k] for k in ("images", "batches", "usd")}})
    total = await asyncio.to_thread(dedupe.estimate_vision, all_paths, model)
    return {"model": model, "price_verified": False, "episodes": rows, "total": total}


class GenerateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    spec: RemixSpec


async def _rewrite(spec: RemixSpec, text: str, rh: dict[str, Any], bible: dict[str, Any], mode: str,
                   external: bool, parent_eps: list[dict[str, Any]] | None = None,
                   bill_to: uuid.UUID | None = None) -> SeriesPlan:
    llm = make_llm(model_for("remix_rewrite").model)
    try:
        return await modes.rewrite(llm, spec, text, rh, bible, mode, external, parent_eps)
    except ContentBlocked as e:
        raise HTTPException(422, str(e)) from e
    except LLMRefused as e:
        raise HTTPException(422, f"model declined: {e}") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    finally:  # ledgered as llm.remix_rewrite on the project the remix came from (or the fork it made)
        if bill_to is not None:
            await usage.record_llm_calls(bill_to, llm.calls)


@api.post("/projects/{project_id}/generate", status_code=201)
async def generate(project_id: uuid.UUID, body: GenerateRequest, session: Session) -> dict[str, Any]:
    """External footage (all registered episodes) -> a NEW project; characters must be redesigned."""
    p = await _project(session, project_id)
    eps = tasks.read_manifest(str(project_id))["episodes"]
    ready = {int(k): e for k, e in eps.items() if e.get("status") in ("deconstructed", "described")}
    if not ready or len(ready) != len(eps):
        raise HTTPException(409, "deconstruct every registered episode first")
    _need_claude(p.content_mode)
    per_ep = {k: tasks.episode_shots(e) for k, e in ready.items()}
    text = "\n\n".join(f"第{k}集 原片\n" + series.remix_source_text(per_ep[k], tasks.episode_notes(ready[k]))
                       for k in sorted(ready))
    rh = rhythm.series_rhythm(per_ep)
    mode, user_id, aspect, src_title = p.content_mode, p.user_id, p.aspect_ratio, p.title
    await session.close()
    plan = await _rewrite(body.spec, text, rh, {"rhythm": rh}, mode, external=True, bill_to=project_id)
    async with session.begin():
        src = await session.get(Project, project_id, with_for_update=True)
        assert src is not None
        src.series_bible_json = rhythm.with_rhythm(src.series_bible_json or {}, rh)
        bible = {**series.bible_from_plan(plan), "rhythm": rh,
                 "remix": {"modes": list(body.spec.modes), "source_project_id": str(project_id), "external": True}}
        new = Project(user_id=user_id, title=body.title or plan.title or f"{src_title}（二创）", category="remix",
                      content_mode=mode, aspect_ratio=aspect, series_bible_json=bible, source_novel_text=text,
                      status="planned")
        session.add(new)
        await session.flush()
        session.add_all([Episode(project_id=new.id, episode_number=o.episode_number, title=o.title,
                                 synopsis=o.synopsis, source_segments=list(o.source_segments))
                         for o in plan.episodes])
        session.add_all(compliance.plan_assets(plan, external=True, project_id=new.id))
    await session.refresh(new)
    return project_view(new)


class ForkRequest(BaseModel):
    source_project_id: uuid.UUID
    title: str | None = Field(default=None, max_length=255)
    spec: RemixSpec | None = None


@api.post("/fork", status_code=201)
async def fork_project(body: ForkRequest, session: Session) -> dict[str, Any]:
    """Own project -> new project inheriting bible, episodes, locked looks and plates; optional remix spec."""
    parent = await _project(session, body.source_project_id)
    if body.spec is not None:
        _need_claude(parent.content_mode)
    await session.close()
    try:
        new = await fork.fork_project(session, body.source_project_id, body.title)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    if body.spec is not None:
        parent_eps = [{"episode_number": e.episode_number, "title": e.title, "synopsis": e.synopsis}
                      for e in (await session.execute(select(Episode).where(
                          Episode.project_id == new.id).order_by(Episode.episode_number))).scalars()]
        bible = new.series_bible_json
        await session.close()
        plan = await _rewrite(body.spec, "", bible.get("rhythm") or {}, bible, new.content_mode, external=False,
                              parent_eps=parent_eps, bill_to=new.id)
        await fork.apply_plan(session, new.id, plan, body.spec)
    n = (await session.execute(select(func.count()).select_from(Episode).where(
        Episode.project_id == new.id))).scalar_one()
    p = await session.get(Project, new.id)
    assert p is not None
    await session.refresh(p)
    return {**project_view(p), "episodes": n}


@api.post("/projects/{project_id}/assets/{asset_id}/redesigned")
async def mark_redesigned(project_id: uuid.UUID, asset_id: uuid.UUID, session: Session) -> dict[str, Any]:
    a = await session.get(LookdevAsset, asset_id)
    if a is None or a.project_id != project_id:
        raise HTTPException(404, "asset not found")
    try:
        compliance.mark_redesigned(a)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await session.commit()
    return {"id": str(a.id), "name": a.name, "redesign_required": a.meta["redesign_required"]}
