import logging
import uuid
from decimal import Decimal
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import SessionLocal
from src.deps import Http, Session, director
from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway import router
from src.domains.gateway.adapters.base import ProviderError
from src.domains.gateway.safety import ContentBlocked, check_prompt
from src.domains.gateway.schemas import JobCreateRequest
from src.models import CreditLedger, Job, ModelCatalog, Project, User

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api/v1")


def model_view(m: ModelCatalog) -> dict[str, Any]:
    # Only abstract model codes reach the frontend: no provider, api_base or keys.
    return {"model_key": m.model_key, "display_name": m.display_name, "task_type": m.task_type,
            "is_nsfw": m.is_nsfw, "is_active": m.is_active, "credits_per_unit": str(m.credits_per_unit),
            "max_duration": m.max_duration, "max_refs": m.max_refs, "fallback_model_key": m.fallback_model_key,
            "resolution_multipliers": m.resolution_multipliers, "health_score": str(m.health_score)}


def job_view(j: Job) -> dict[str, Any]:
    return {"id": str(j.id), "shot_id": str(j.shot_id) if j.shot_id else None, "project_id": str(j.project_id),
            "status": j.status, "model_key": j.model_key, "task_type": j.task_type,
            "credit_estimate": str(j.credit_estimate),
            "actual_credits": None if j.actual_credits is None else str(j.actual_credits),
            "error_message": j.error_message, "created_at": j.created_at.isoformat()}


@api.get("/models")
async def list_models(session: Session) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(ModelCatalog).where(ModelCatalog.is_active).order_by(ModelCatalog.model_key))).scalars()
    return [model_view(m) for m in rows]


def _task_fits_model(task: str, model_task: str) -> bool:
    family = model_task.split(".")[0]
    return task.startswith(family + ".") if family in ("video", "image") else task == model_task


async def _dispatch_bg(job_id: uuid.UUID, client: httpx.AsyncClient) -> None:
    try:
        await router.dispatch_job(SessionLocal, job_id, client)
    except ProviderError as e:  # job row already refunded / marked
        log.warning("dispatch %s failed: %s", job_id, e)


async def submit(req: JobCreateRequest, session: AsyncSession, bg: BackgroundTasks, http: httpx.AsyncClient,
                 meta: dict[str, Any] | None = None) -> Job:
    """All generation goes through here: gates, limits, pre-deduct, then queue."""
    project = await session.get(Project, req.project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    user = await session.get(User, project.user_id)
    model = await session.get(ModelCatalog, req.model_key)
    await session.close()  # ledger owns its own transaction
    assert user is not None
    if project.content_mode == "uncensored_nsfw" and not user.age_verified_18plus:
        raise HTTPException(403, "uncensored_nsfw requires 18+ verification")
    try:
        check_prompt(f"{req.prompt}\n{req.negative_prompt or ''}", project.content_mode,
                     context={"project_id": str(project.id), "model_key": req.model_key})
    except ContentBlocked as e:
        raise HTTPException(422, str(e)) from e
    if model is None or not model.is_active:
        raise HTTPException(422, f"model {req.model_key} unavailable")
    if not _task_fits_model(req.task_type, model.task_type):
        raise HTTPException(422, f"{req.model_key} cannot run {req.task_type}")
    if req.task_type == "face.swap" and not req.likeness_consent:
        raise HTTPException(422, "face.swap requires likeness_consent (fictional character or full likeness rights)")
    if model.max_duration and req.duration > model.max_duration:
        raise HTTPException(422, f"{req.model_key} max duration is {model.max_duration}s")
    if len(req.refs.image_urls) + len(req.refs.video_urls) + len(req.refs.audio_urls) > model.max_refs:
        raise HTTPException(422, f"{req.model_key} accepts at most {model.max_refs} references")
    try:
        job = await router.create_job(session, user_id=user.id, req=req, meta=meta)
    except (ledger.InsufficientCredits, ledger.DailyLimitExceeded) as e:
        raise HTTPException(402, str(e)) from e
    except ledger.ModelUnavailable as e:
        raise HTTPException(422, f"model {e} unavailable") from e
    from src.domains.storyboard.shot_orchestrator import enqueue_local
    from src.queue import celery_app, queue_for
    if model.provider == "local":
        enqueue_local(job.task_type, str(job.id))
    elif settings.dispatch_mode == "inline":
        bg.add_task(_dispatch_bg, job.id, http)
    else:
        celery_app.send_task("gateway.dispatch", args=[str(job.id)],
                             queue=queue_for(job.task_type, job.model_key, model.provider))
    return job


@api.post("/jobs", status_code=201)
async def create_job(req: JobCreateRequest, session: Session, bg: BackgroundTasks, http: Http) -> dict[str, Any]:
    return job_view(await submit(req, session, bg, http))


@api.post("/webhooks/{provider}")
async def provider_webhook(provider: str, request: Request, http: Http,
                           x_webhook_signature: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    try:
        return await router.handle_callback(SessionLocal, http, provider, await request.body(), x_webhook_signature)
    except router.WebhookAuthError as e:
        raise HTTPException(401, "bad signature") from e
    except LookupError as e:
        raise HTTPException(404, "unknown provider job") from e


@api.get("/me")
async def me(session: Session) -> dict[str, Any]:
    u = await director(session)
    return {"id": str(u.id), "username": u.username, "credits": str(u.credits),
            "daily_limit_credits": str(u.daily_limit_credits), "age_verified_18plus": u.age_verified_18plus}


@api.get("/ledger")
async def list_ledger(session: Session, user_id: uuid.UUID | None = None, limit: int = 100) -> list[dict[str, Any]]:
    uid = user_id or (await director(session)).id
    rows = (await session.execute(select(CreditLedger).where(CreditLedger.user_id == uid)
                                  .order_by(CreditLedger.created_at.desc()).limit(limit))).scalars()
    return [{"id": str(r.id), "job_id": str(r.job_id) if r.job_id else None, "delta": str(r.delta),
             "balance": str(r.balance), "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rows]


class ModelPatch(BaseModel):
    is_active: bool | None = None
    credits_per_unit: Decimal | None = None
    fallback_model_key: str | None = None


@api.get("/admin/models")
async def admin_models(session: Session) -> list[dict[str, Any]]:
    return [model_view(m) for m in (await session.execute(select(ModelCatalog).order_by(ModelCatalog.model_key)))
            .scalars()]


@api.patch("/admin/models/{model_key}")
async def patch_model(model_key: str, patch: ModelPatch, session: Session) -> dict[str, Any]:
    m = await session.get(ModelCatalog, model_key)
    if m is None:
        raise HTTPException(404, "model not found")
    for k, v in patch.model_dump(exclude_unset=True).items():
        if k == "credits_per_unit" and v is not None and v < 0:
            raise HTTPException(422, "price must be >= 0")
        setattr(m, k, v)
    await session.commit()
    await session.refresh(m)
    return model_view(m)


@api.get("/admin/jobs")
async def admin_jobs(session: Session, limit: int = 100) -> list[dict[str, Any]]:
    return [job_view(j) for j in (await session.execute(
        select(Job).order_by(Job.created_at.desc()).limit(limit))).scalars()]


@api.post("/admin/jobs/{job_id}/refund")
async def admin_refund(job_id: uuid.UUID, session: Session) -> dict[str, Any]:
    await session.close()
    try:
        job = await ledger.refund_job(session, job_id, reason="cancel_refund", status="cancelled",
                                      error="manual refund")
    except ledger.InvalidJobState as e:
        raise HTTPException(409, str(e)) from e
    return job_view(job)


@api.post("/admin/jobs/{job_id}/retry", status_code=201)
async def admin_retry(job_id: uuid.UUID, session: Session, bg: BackgroundTasks, http: Http) -> dict[str, Any]:
    old = await session.get(Job, job_id)
    if old is None or "request" not in old.request_payload:
        raise HTTPException(404, "job not retryable")
    req = JobCreateRequest.model_validate(old.request_payload["request"])
    meta: dict[str, Any] = {k: old.request_payload[k] for k in ("render_tier", "chain") if k in old.request_payload}
    return job_view(await submit(req, session, bg, http, meta=meta))
