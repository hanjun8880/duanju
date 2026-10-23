import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import media
from src.deps import Http, Session
from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway.api import job_view, submit
from src.domains.storyboard import shot_orchestrator as orch
from src.models import Asset, Project, Shot

api = APIRouter(prefix="/api/v1")


def shot_view(s: Shot, clip: Path | None = None) -> dict[str, Any]:
    return {"id": str(s.id), "episode_number": s.episode_number, "shot_order": s.shot_order,
            "template_id": str(s.template_id) if s.template_id else None, "render_tier": s.render_tier,
            "duration": s.duration, "resolution": s.resolution, "shot_prompt": s.shot_prompt,
            "dialogue_transcript": s.dialogue_transcript, "first_frame_url": s.first_frame_url,
            "last_frame_url": s.last_frame_url, "refs": s.refs, "source_remix_meta": s.source_remix_meta,
            "status": s.status, "preview_url": media.url_for(clip) if clip and media.root() in clip.parents else None}


@api.get("/projects/{project_id}/shots")
async def list_shots(project_id: uuid.UUID, session: Session, episode: int | None = None) -> list[dict[str, Any]]:
    q = select(Shot).where(Shot.project_id == project_id)
    if episode:
        q = q.where(Shot.episode_number == episode)
    shots = (await session.execute(q.order_by(Shot.episode_number, Shot.shot_order))).scalars().all()
    clips: dict[uuid.UUID, Path] = {}
    for ep in {s.episode_number for s in shots}:
        clips |= await orch.best_clips(session, project_id, ep)
    return [shot_view(s, clips.get(s.id)) for s in shots]


class ShotPatch(BaseModel):
    shot_prompt: str | None = Field(default=None, min_length=1)
    dialogue_transcript: str | None = None
    duration: int | None = Field(default=None, ge=4, le=30)
    template_id: uuid.UUID | None = None


@api.patch("/shots/{shot_id}")
async def patch_shot(shot_id: uuid.UUID, body: ShotPatch, session: Session) -> dict[str, Any]:
    s = await session.get(Shot, shot_id)
    if s is None:
        raise HTTPException(404, "shot not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    await session.commit()
    await session.refresh(s)
    return shot_view(s)


class RenderRequest(BaseModel):
    tier: Literal["L0_animatic", "L1_draft", "L2_master", "L3_post"]
    chain: bool = True


@api.post("/shots/{shot_id}/render", status_code=201)
async def render_shot(shot_id: uuid.UUID, body: RenderRequest, session: Session, bg: BackgroundTasks, http: Http,
                      response: Response) -> dict[str, Any]:
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(404, "shot not found")
    if body.tier in ("L0_animatic", "L3_post"):
        response.status_code = 202
        return await _local_render(session, shot, body.tier)
    return await _cloud_render(session, shot_id, body.tier, body.chain, bg, http)


async def _local_render(session: AsyncSession, shot: Shot, tier: str) -> dict[str, Any]:
    project = await session.get(Project, shot.project_id)
    assert project is not None
    model_key, task = ("local_animatic", "post.animatic") if tier == "L0_animatic" \
        else ("realesrgan_x4plus", "post.upscale")
    await session.close()
    job = await ledger.create_job_with_prededuct(
        session, user_id=project.user_id, project_id=shot.project_id, model_key=model_key, task_type=task,
        request_payload={"shot_id": str(shot.id), "render_tier": tier}, billable_units=shot.duration,
        resolution=shot.resolution, shot_id=shot.id)
    orch.enqueue_local(task, str(job.id))
    return {**job_view(job), "queue": "local_post"}


async def _cloud_render(session: AsyncSession, shot_id: uuid.UUID, tier: str, chain: bool, bg: BackgroundTasks,
                        http: Http, batch: bool = False) -> dict[str, Any]:
    try:
        req = await orch.shot_request(session, shot_id, tier, chain=chain)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    job = await submit(req, session, bg, http, meta={"render_tier": tier, "chain": chain, "batch": batch})
    return job_view(job)


@api.post("/projects/{project_id}/episodes/{n}/render", status_code=202)
async def render_episode(project_id: uuid.UUID, n: int, body: RenderRequest, session: Session, bg: BackgroundTasks,
                         http: Http) -> dict[str, Any]:
    """Whole episode at one tier. Chained L2 renders one shot at a time (each needs the previous last frame);
    everything else is queued at once."""
    shots = (await session.execute(select(Shot).where(Shot.project_id == project_id, Shot.episode_number == n)
                                   .order_by(Shot.shot_order))).scalars().all()
    if not shots:
        raise HTTPException(404, f"episode {n} has no shots")
    if body.tier == "L3_post":
        raise HTTPException(422, "L3 runs per shot on a finished master")
    if body.tier == "L2_master" and body.chain:
        return {"jobs": [await _cloud_render(session, shots[0].id, body.tier, True, bg, http, batch=True)],
                "mode": "sequential"}
    jobs = []
    for s in shots:
        jobs.append(await _local_render(session, s, body.tier) if body.tier == "L0_animatic"
                    else await _cloud_render(session, s.id, body.tier, body.chain, bg, http))
    return {"jobs": jobs, "mode": "parallel"}


class AssembleRequest(BaseModel):
    episode_number: int | None = Field(default=None, ge=1, description="omit to export the whole series")


@api.post("/projects/{project_id}/assemble", status_code=202)
async def assemble(project_id: uuid.UUID, body: AssembleRequest, session: Session) -> dict[str, Any]:
    """Episode master from each shot's best clip, or the whole series from each episode's latest master."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    if body.episode_number is not None:
        clips = await orch.best_clips(session, project_id, body.episode_number)
        if not clips:
            raise HTTPException(422, f"episode {body.episode_number} has no finished shot yet")
    else:
        if not (await session.execute(select(Asset.id).where(Asset.project_id == project_id,
                                                             Asset.kind == "episode").limit(1))).first():
            raise HTTPException(422, "assemble at least one episode first")
    await session.close()
    job = await ledger.create_job_with_prededuct(
        session, user_id=project.user_id, project_id=project_id, model_key="local_assemble", task_type="post.assemble",
        request_payload={"episode_number": body.episode_number}, billable_units=1, resolution="1080p")
    orch.enqueue_local("post.assemble", str(job.id))
    return {**job_view(job), "queue": "local_post"}


@api.get("/projects/{project_id}/outputs")
async def list_outputs(project_id: uuid.UUID, session: Session) -> list[dict[str, Any]]:
    """Episode masters and whole-series exports, newest first."""
    rows = (await session.execute(select(Asset).where(Asset.project_id == project_id,
                                                      Asset.kind.in_(["episode", "series"]))
                                  .order_by(Asset.created_at.desc()))).scalars()
    out = []
    for a in rows:
        f = orch.clip_file(a.s3_key)
        if f is not None and media.root() in f.parents:
            out.append({"id": str(a.id), "kind": a.kind, "url": media.url_for(f), "meta": a.meta,
                        "created_at": a.created_at.isoformat()})
    return out
