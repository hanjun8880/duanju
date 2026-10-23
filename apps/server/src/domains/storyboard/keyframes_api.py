"""Keyframe review endpoints: generate (per shot / whole episode), approve, reject, list for the grid."""
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.deps import Http, Session
from src.domains.gateway.api import job_view, submit
from src.domains.storyboard import keyframes as kf
from src.models import Job, Shot

api = APIRouter(prefix="/api/v1")


async def _reconcile(session: AsyncSession, shots: list[Shot]) -> None:
    """A pending keyframe whose newest job was refunded (vendor error, no key) goes back to none."""
    pending = [s for s in shots if s.keyframe_status == "pending"]
    for s in pending:
        last = (await session.execute(select(Job.status).where(
            Job.shot_id == s.id, Job.request_payload["keyframe_shot_id"].astext == str(s.id))
            .order_by(Job.created_at.desc()).limit(1))).scalar_one_or_none()
        if last in (None, "failed", "dead_letter", "cancelled"):
            s.keyframe_status = "none"
    if pending:
        await session.commit()


def keyframe_view(s: Shot) -> dict[str, Any]:
    return {"shot_id": str(s.id), "episode_number": s.episode_number, "shot_order": s.shot_order,
            "shot_kind": s.shot_kind, "shot_prompt": s.shot_prompt, "keyframe_url": s.keyframe_url,
            "keyframe_status": s.keyframe_status}


async def _generate(session: AsyncSession, shot_id: uuid.UUID, bg: BackgroundTasks, http: Http,
                    force: bool) -> dict[str, Any]:
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(404, "shot not found")
    req = await kf.keyframe_request(session, shot)
    meta = kf.keyframe_meta(shot)
    prev = shot.keyframe_status
    shot.keyframe_status = "pending"
    await session.commit()
    try:
        job = await submit(req, session, bg, http, meta=meta, force=force)
    except HTTPException:
        await session.close()
        async with session.begin():
            s = await session.get(Shot, shot_id)
            if s is not None and s.keyframe_status == "pending":
                s.keyframe_status = prev
        raise
    return job_view(job)


@api.post("/shots/{shot_id}/keyframe", status_code=201)
async def generate_keyframe(shot_id: uuid.UUID, session: Session, bg: BackgroundTasks, http: Http,
                            force: bool = False) -> dict[str, Any]:
    """One keyframe still through the gateway (credits, budget, cache). force=true re-rolls past the cache."""
    return await _generate(session, shot_id, bg, http, force)


class EpisodeKeyframes(BaseModel):
    only_missing: bool = True  # skip shots whose keyframe is ready / approved / pending
    force: bool = False


@api.post("/projects/{project_id}/episodes/{n}/keyframes", status_code=201)
async def generate_episode_keyframes(project_id: uuid.UUID, n: int, session: Session, bg: BackgroundTasks,
                                     http: Http, body: EpisodeKeyframes | None = None) -> dict[str, Any]:
    body = body or EpisodeKeyframes()
    shots = (await session.execute(select(Shot).where(Shot.project_id == project_id, Shot.episode_number == n)
                                   .order_by(Shot.shot_order))).scalars().all()
    if not shots:
        raise HTTPException(404, f"第 {n} 集还没有镜头")
    todo = [s.id for s in shots if not body.only_missing or s.keyframe_status in ("none", "rejected")]
    jobs: list[dict[str, Any]] = []
    for i, sid in enumerate(todo):
        try:
            jobs.append(await _generate(session, sid, bg, http, body.force))
        except HTTPException as e:  # budget / credits ran out: keep what was queued, report the rest
            if not jobs:
                raise
            return {"jobs": jobs, "skipped": [str(x) for x in todo[i:]], "error": e.detail}
    return {"jobs": jobs, "skipped": [], "error": None}


@api.get("/projects/{project_id}/episodes/{n}/keyframes")
async def list_keyframes(project_id: uuid.UUID, n: int, session: Session) -> list[dict[str, Any]]:
    shots = list((await session.execute(select(Shot).where(Shot.project_id == project_id, Shot.episode_number == n)
                                        .order_by(Shot.shot_order))).scalars())
    await _reconcile(session, shots)
    return [keyframe_view(s) for s in shots]


async def _review(session: AsyncSession, shot_id: uuid.UUID, approve: bool) -> dict[str, Any]:
    await session.close()
    try:
        return keyframe_view(await kf.review(session, shot_id, approve))
    except LookupError as e:
        raise HTTPException(404, "shot not found") from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@api.post("/shots/{shot_id}/keyframe/approve")
async def approve_keyframe(shot_id: uuid.UUID, session: Session) -> dict[str, Any]:
    return await _review(session, shot_id, True)


@api.post("/shots/{shot_id}/keyframe/reject")
async def reject_keyframe(shot_id: uuid.UUID, session: Session) -> dict[str, Any]:
    return await _review(session, shot_id, False)
