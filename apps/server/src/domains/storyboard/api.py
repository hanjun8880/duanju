import uuid
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.deps import Http, Session
from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway.api import job_view, submit
from src.domains.storyboard import shot_orchestrator as orch
from src.models import Project, Shot

api = APIRouter(prefix="/api/v1")


def shot_view(s: Shot) -> dict[str, Any]:
    return {"id": str(s.id), "episode_number": s.episode_number, "shot_order": s.shot_order,
            "template_id": str(s.template_id) if s.template_id else None, "render_tier": s.render_tier,
            "duration": s.duration, "resolution": s.resolution, "shot_prompt": s.shot_prompt,
            "dialogue_transcript": s.dialogue_transcript, "first_frame_url": s.first_frame_url,
            "last_frame_url": s.last_frame_url, "refs": s.refs, "source_remix_meta": s.source_remix_meta,
            "status": s.status}


@api.get("/projects/{project_id}/shots")
async def list_shots(project_id: uuid.UUID, session: Session, episode: int | None = None) -> list[dict[str, Any]]:
    q = select(Shot).where(Shot.project_id == project_id)
    if episode:
        q = q.where(Shot.episode_number == episode)
    return [shot_view(s) for s in (await session.execute(q.order_by(Shot.episode_number, Shot.shot_order))).scalars()]


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
        project = await session.get(Project, shot.project_id)
        assert project is not None
        model_key, task = ("local_animatic", "post.animatic") if body.tier == "L0_animatic" \
            else ("realesrgan_x4plus", "post.upscale")
        await session.close()
        job = await ledger.create_job_with_prededuct(
            session, user_id=project.user_id, project_id=shot.project_id, model_key=model_key, task_type=task,
            request_payload={"shot_id": str(shot_id), "render_tier": body.tier}, billable_units=shot.duration,
            resolution=shot.resolution, shot_id=shot_id)
        orch.enqueue_local(task, str(job.id))
        response.status_code = 202
        return {**job_view(job), "queue": "local_post"}
    try:
        req = await orch.shot_request(session, shot_id, body.tier, chain=body.chain)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    job = await submit(req, session, bg, http, meta={"render_tier": body.tier, "chain": body.chain})
    return job_view(job)
