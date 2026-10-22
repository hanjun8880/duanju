import logging
from typing import Annotated, Any

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import SessionLocal, get_session
from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway import router
from src.domains.gateway.adapters.base import ProviderError
from src.domains.gateway.safety import ContentBlocked, check_prompt
from src.domains.gateway.schemas import JobCreateRequest
from src.models import ModelCatalog, Project, User

log = logging.getLogger(__name__)
app = FastAPI(title="Director Workbench API")
Session = Annotated[AsyncSession, Depends(get_session)]
_http: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient()
    return _http


Http = Annotated[httpx.AsyncClient, Depends(get_http_client)]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/models")
async def list_models(session: Session) -> list[dict[str, object]]:
    # Only abstract model codes reach the frontend: no provider, api_base or keys.
    rows = (await session.execute(
        select(ModelCatalog).where(ModelCatalog.is_active).order_by(ModelCatalog.model_key))).scalars()
    return [{"model_key": m.model_key, "display_name": m.display_name, "task_type": m.task_type,
             "is_nsfw": m.is_nsfw, "credits_per_unit": str(m.credits_per_unit), "max_duration": m.max_duration,
             "resolution_multipliers": m.resolution_multipliers} for m in rows]


def _task_fits_model(task: str, model_task: str) -> bool:
    family = model_task.split(".")[0]
    return task.startswith(family + ".") if family in ("video", "image") else task == model_task


async def _dispatch_bg(job_id: Any, client: httpx.AsyncClient) -> None:
    try:
        await router.dispatch_job(SessionLocal, job_id, client)
    except ProviderError as e:  # job row already refunded / marked; nothing to raise to
        log.warning("dispatch %s failed: %s", job_id, e)


@app.post("/api/v1/jobs", status_code=201)
async def create_job(req: JobCreateRequest, session: Session, bg: BackgroundTasks, http: Http) -> dict[str, Any]:
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
    n_refs = len(req.refs.image_urls) + len(req.refs.video_urls) + len(req.refs.audio_urls)
    if n_refs > model.max_refs:
        raise HTTPException(422, f"{req.model_key} accepts at most {model.max_refs} references")
    try:
        job = await router.create_job(session, user_id=user.id, req=req)
    except (ledger.InsufficientCredits, ledger.DailyLimitExceeded) as e:
        raise HTTPException(402, str(e)) from e
    except ledger.ModelUnavailable as e:
        raise HTTPException(422, f"model {e} unavailable") from e
    if model.provider != "local":  # local models run on the post-processing queue (Phase 3)
        bg.add_task(_dispatch_bg, job.id, http)
    return {"id": str(job.id), "status": job.status, "model_key": job.model_key, "task_type": job.task_type,
            "credit_estimate": str(job.credit_estimate)}


@app.post("/api/v1/webhooks/{provider}")
async def provider_webhook(provider: str, request: Request, http: Http,
                           x_webhook_signature: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    try:
        return await router.handle_callback(SessionLocal, http, provider, await request.body(), x_webhook_signature)
    except router.WebhookAuthError as e:
        raise HTTPException(401, "bad signature") from e
    except LookupError as e:
        raise HTTPException(404, "unknown provider job") from e
