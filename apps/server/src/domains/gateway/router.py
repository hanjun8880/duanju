"""Job creation, vendor dispatch with one-hop failover, and webhook settlement."""
import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import time
import uuid
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import media
from src.config import settings
from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway.adapters import ADAPTERS
from src.domains.gateway.adapters.base import (
    CallbackResult,
    ProviderError,
    ProviderNotConfigured,
    ProviderRejected,
    ProviderRetryable,
)
from src.domains.gateway.schemas import JobCreateRequest, preflight
from src.models import Asset, Job, ModelCatalog, Project, Shot

log = logging.getLogger(__name__)
__all__ = ["ProviderNotConfigured", "ProviderRejected", "ProviderUnavailable", "WebhookAuthError"]


class ProviderUnavailable(ProviderError):
    """Primary and fallback both failed (money already refunded)."""


class WebhookAuthError(Exception):
    pass


def webhook_url(provider: str) -> str:
    return f"{os.environ.get('PUBLIC_BASE_URL', 'http://localhost:8000').rstrip('/')}/api/v1/webhooks/{provider}"


def content_hash(model_key: str, task: str, canonical: dict[str, Any]) -> str:
    """Same model + same cleaned request => same output: sha256(model_key + sorted JSON)."""
    return hashlib.sha256((model_key + json.dumps({"task_type": task, **canonical}, sort_keys=True,
                                                  ensure_ascii=False)).encode()).hexdigest()


async def _stage_of(session: AsyncSession, req: JobCreateRequest, task: str, meta: dict[str, Any]) -> str:
    if meta.get("stage"):
        return str(meta["stage"])
    if meta.get("lookdev_id"):
        return "lookdev_image"
    if meta.get("keyframe_shot_id"):
        return "keyframe_image"
    if not task.startswith("video."):
        return task.split(".")[0]
    if req.resolution == "480p" or meta.get("render_tier") == "L1_draft":
        return "video_draft"
    if req.resolution == "1080p":
        return "video_final"
    kind = (await session.execute(select(Shot.shot_kind).where(Shot.id == req.shot_id))).scalar_one_or_none() \
        if req.shot_id else None
    return f"video_{kind}" if kind in ("dialogue", "action", "establishing", "insert") else "video_dialogue"


async def create_job(session: AsyncSession, *, user_id: uuid.UUID, req: JobCreateRequest,
                     model_key: str | None = None, failover_of: uuid.UUID | None = None,
                     meta: dict[str, Any] | None = None, force: bool = False) -> Job:
    """Pre-deducted queued job, or (identical earlier render still on disk, not forced) a zero-cost success job
    with request_payload.cached_from. Raises ledger.BudgetExceeded when a USD budget would be exceeded."""
    meta = dict(meta or {})
    force = bool(meta.pop("force", False)) or force
    task, canonical = preflight(req)
    key = model_key or req.model_key
    stage = await _stage_of(session, req, task, meta)
    meta.pop("stage", None)
    payload: dict[str, Any] = {**meta, "request": req.model_dump(mode="json"), "task_type": task,
                               "canonical": canonical, "billable_units": req.billable_units(),
                               "resolution": req.resolution, "stage": stage}
    if force:  # remembered so an episode batch keeps re-rolling past the cache on every later shot
        payload["forced"] = True
    h = content_hash(key, task, canonical)
    if failover_of:
        payload["failover_of"] = str(failover_of)
    elif not force:
        hit = await ledger.find_cached(session, req.project_id, h)
        await session.close()
        if hit is not None:
            log.info("cache hit %s -> reuse job %s", h[:12], hit[0].id)
            return await ledger.record_cached_job(session, source=hit[0], assets=hit[1], shot_id=req.shot_id,
                                                  task_type=task, request_payload=payload)
    await session.close()  # ledger owns its own transaction
    return await ledger.create_job_with_prededuct(
        session, user_id=user_id, project_id=req.project_id, model_key=key, task_type=task,
        request_payload=payload, billable_units=req.billable_units(), resolution=req.resolution, shot_id=req.shot_id,
        content_hash=h)


async def _refund(sm: async_sessionmaker[AsyncSession], job_id: uuid.UUID, reason: str, error: str,
                  status: str = "failed") -> None:
    async with sm() as s:
        await ledger.refund_job(s, job_id, reason=reason, status=status, error=error)


async def _shot_failed(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, job: Job) -> None:
    """A shot render is out of options: mark it failed; a batch resumes at the next non-continuous shot."""
    p = job.request_payload
    if not job.shot_id or "render_tier" not in p:
        return
    from src.domains.storyboard import shot_orchestrator as orch
    try:
        await orch.on_render_failed(sm, client, job.shot_id, p["render_tier"], chain=bool(p.get("chain")),
                                    final=bool(p.get("final")), batch=bool(p.get("batch")),
                                    force=bool(p.get("forced")))
    except Exception:  # never mask the original provider error
        log.exception("after-failure handling for shot %s", job.shot_id)


async def _failover(sm: async_sessionmaker[AsyncSession], job: Job, model: ModelCatalog,
                    client: httpx.AsyncClient, error: str) -> Job:
    if not model.fallback_model_key or job.request_payload.get("failover_of"):
        await _shot_failed(sm, client, job)
        raise ProviderUnavailable(error)  # one hop only: seedance<->wan must not ping-pong
    async with sm() as s:
        owner = (await s.execute(select(Project.user_id).where(Project.id == job.project_id))).scalar_one()
    req = JobCreateRequest.model_validate(job.request_payload["request"])
    async with sm() as s:
        meta: dict[str, Any] = {k: job.request_payload[k] for k in ("render_tier", "chain", "batch", "final",
                                                                    "forced") if k in job.request_payload}
        new = await create_job(s, user_id=owner, req=req, model_key=model.fallback_model_key, failover_of=job.id,
                               meta=meta)
    log.warning("failover %s -> %s (%s)", job.model_key, new.model_key, error)
    return await dispatch_job(sm, new.id, client)


async def dispatch_job(sm: async_sessionmaker[AsyncSession], job_id: uuid.UUID, client: httpx.AsyncClient) -> Job:
    async with sm() as s:
        job = await s.get(Job, job_id)
        assert job is not None
        model = await s.get(ModelCatalog, job.model_key)
        assert model is not None
    if job.status != "queued":  # e.g. a cache hit (already success): never send it to a vendor
        return job
    adapter = ADAPTERS.get(model.provider)
    canonical = dict(job.request_payload["canonical"])
    canonical["callback_url"] = job.request_payload["request"].get("callback_url") or webhook_url(model.provider)
    try:
        if adapter is None:
            raise ProviderNotConfigured(f"no HTTP adapter for provider {model.provider}")
        pid = await adapter.submit(client, api_base=model.api_base, model_key=model.model_key,
                                   task_type=job.task_type, canonical=canonical)
    except ProviderNotConfigured as e:
        await _refund(sm, job.id, "cancel_refund", str(e), status="cancelled")
        await _shot_failed(sm, client, job)
        raise
    except ProviderRejected as e:
        await _refund(sm, job.id, "provider_4xx_refund", str(e))
        await _shot_failed(sm, client, job)
        raise
    except ProviderRetryable as e:
        await _refund(sm, job.id, "timeout_refund" if e.timeout else "provider_5xx_refund", str(e))
        return await _failover(sm, job, model, client, str(e))
    sync = pid if isinstance(pid, CallbackResult) else None
    async with sm() as s, s.begin():
        locked = (await s.execute(select(Job).where(Job.id == job.id).with_for_update())).scalar_one()
        if locked.status == "queued":
            locked.status, locked.provider_job_id = "processing", sync.provider_job_id if sync else pid
    if sync is not None:  # synchronous vendor (e.g. Ark images): settle right away
        await apply_result(sm, client, job, model, sync)
    elif hasattr(adapter, "poll"):
        await poll_until_done(sm, client, job.id)
    async with sm() as s:
        fresh = await s.get(Job, job.id)
        assert fresh is not None
        return fresh


async def poll_until_done(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, job_id: uuid.UUID,
                          interval: float | None = None, max_wait: float | None = None) -> dict[str, Any] | None:
    """For vendors without signed callbacks (Ark). Transient poll errors are retried until the deadline."""
    interval = float(os.environ.get("POLL_INTERVAL_S", "10")) if interval is None else interval
    max_wait = float(os.environ.get("POLL_MAX_WAIT_S", "7200")) if max_wait is None else max_wait
    async with sm() as s:
        job = await s.get(Job, job_id)
        assert job is not None and job.provider_job_id
        model = await s.get(ModelCatalog, job.model_key)
        assert model is not None
    adapter = ADAPTERS[model.provider]
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            cb = await adapter.poll(client, api_base=model.api_base, provider_job_id=job.provider_job_id)
        except ProviderRejected as e:
            await _refund(sm, job.id, "provider_4xx_refund", str(e))
            await _shot_failed(sm, client, job)
            return None
        except ProviderRetryable as e:
            log.warning("poll %s: %s", job.id, e)
            cb = None
        if cb is not None:
            return await apply_result(sm, client, job, model, cb)
        await asyncio.sleep(interval)
    await _refund(sm, job.id, "timeout_refund", f"no result after {max_wait:.0f}s")
    try:
        await _failover(sm, job, model, client, "poll timeout")
    except ProviderError as e:
        log.warning("failover after timeout failed: %s", e)
    return None


async def resume_polls(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient) -> None:
    """After a restart, pick polling back up for jobs a vendor is still working on."""
    async with sm() as s:
        ids = (await s.execute(select(Job.id).join(ModelCatalog, Job.model_key == ModelCatalog.model_key).where(
            Job.status == "processing", Job.provider_job_id.is_not(None),
            ModelCatalog.provider.in_([k for k, a in ADAPTERS.items() if hasattr(a, "poll")])))).scalars().all()
    await asyncio.gather(*(poll_until_done(sm, client, i) for i in ids), return_exceptions=True)


def _suffix(url: str, kind: str) -> str:
    ext = PurePosixPath(urlparse(url).path).suffix.lower()
    if ext and mimetypes.guess_type("x" + ext)[0]:
        return ext
    return ".mp4" if kind == "video" else ".jpeg"


async def _keep_copy(client: httpx.AsyncClient, job: Job, kind: str, url: str) -> tuple[str, dict[str, Any]]:
    try:
        local = await media.download(client, url, f"renders/{job.project_id}", _suffix(url, kind))
        return local, {"source_url": url, "url": local}
    except (httpx.HTTPError, OSError) as e:  # keep the vendor URL; it is still valid for a while
        log.warning("download %s failed: %s", url, e)
        return f"pending/{job.id}/{kind}", {"source_url": url, "url": url, "s3": "pending_download"}


async def apply_result(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, job: Job,
                       model: ModelCatalog, cb: CallbackResult) -> dict[str, Any]:
    """One place for callback and poll results: settle or refund+failover, keep outputs, fire follow-ups."""
    try:
        if cb.ok:
            units = cb.billed_seconds or job.request_payload["billable_units"]
            actual = ledger.estimate_credits(model, units, job.request_payload["resolution"])
            main_kind = "image" if job.task_type.startswith("image.") else "video"
            outputs, urls = [], {}
            for kind, url in ((main_kind, cb.video_url), ("last_frame", cb.last_frame_url)):
                if url:
                    key, meta = await _keep_copy(client, job, kind, url)
                    urls[kind] = meta["url"]
                    outputs.append(Asset(job_id=job.id, project_id=job.project_id, kind=kind, s3_key=key,
                                         meta={**meta, "shot_id": str(job.shot_id) if job.shot_id else None,
                                               "render_tier": job.request_payload.get("render_tier")}))
            async with sm() as s:
                await ledger.settle_job(s, job.id, actual, extra=outputs)
            await _after_success(sm, client, job, urls)
            return {"status": "success", "job_id": str(job.id), "actual_credits": str(actual)}
        await _refund(sm, job.id, "provider_5xx_refund", cb.error or "provider reported failure")
    except ledger.InvalidJobState:
        return {"status": "duplicate", "job_id": str(job.id)}
    try:
        new = await _failover(sm, job, model, client, cb.error or "failed")
        return {"status": "failed", "job_id": str(job.id), "failover_job_model": new.model_key}
    except ProviderError as e:
        return {"status": "failed", "job_id": str(job.id), "failover_job_model": None, "error": str(e)}


async def _after_success(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, job: Job,
                         urls: dict[str, str]) -> None:
    p = job.request_payload
    if p.get("lookdev_id") and urls.get("image"):
        from src.domains.lookdev.api import on_lookdev_rendered
        refs = (p.get("request") or {}).get("refs") or {}
        pure_t2i = not (refs.get("image_urls") or refs.get("video_urls") or refs.get("audio_urls"))
        async with sm() as s:
            await on_lookdev_rendered(s, uuid.UUID(p["lookdev_id"]), urls["image"], pure_t2i=pure_t2i)
    if p.get("keyframe_shot_id") and urls.get("image"):
        from src.domains.storyboard.keyframes import on_keyframe_rendered
        async with sm() as s:
            await on_keyframe_rendered(s, uuid.UUID(p["keyframe_shot_id"]), urls["image"])
    if job.shot_id and "render_tier" in p:
        from src.domains.storyboard import shot_orchestrator as orch
        async with sm() as s:
            await orch.on_shot_rendered(s, job.shot_id, p["render_tier"], urls.get("last_frame"), bool(p.get("chain")))
        if p.get("batch"):
            await orch.render_next(sm, client, job.shot_id, p["render_tier"], bool(p.get("chain")),
                                   bool(p.get("final")), force=bool(p.get("forced")))


async def run_cached_followups(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, job: Job) -> None:
    """A content-hash cache hit is born 'success' and never reaches a vendor or callback, so fire the same
    follow-ups a settled render would (look-dev candidate, keyframe, shot status, next shot of a batch)."""
    async with sm() as s:
        urls = {a.kind: str(a.meta.get("url") or a.s3_key) for a in (await s.execute(
            select(Asset).where(Asset.job_id == job.id))).scalars()}
    await _after_success(sm, client, job, urls)


async def queue_dispatch(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, job: Job,
                         provider: str) -> None:
    from src.queue import celery_app, queue_for
    if settings.dispatch_mode == "inline":
        try:
            await dispatch_job(sm, job.id, client)
        except ProviderError as e:
            log.warning("dispatch %s failed: %s", job.id, e)
    else:
        celery_app.send_task("gateway.dispatch", args=[str(job.id)],
                             queue=queue_for(job.task_type, job.model_key, provider))


async def handle_callback(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, provider: str,
                          raw: bytes, signature: str | None) -> dict[str, Any]:
    adapter = ADAPTERS.get(provider)
    if adapter is None or not adapter.verify_signature(raw, signature):
        raise WebhookAuthError(provider)
    cb = adapter.parse_callback(json.loads(raw))
    async with sm() as s:
        row = (await s.execute(select(Job, ModelCatalog).join(ModelCatalog, Job.model_key == ModelCatalog.model_key)
                               .where(Job.provider_job_id == cb.provider_job_id, ModelCatalog.provider == provider))
               ).first()
    if row is None:
        raise LookupError(cb.provider_job_id)
    job, model = row
    return await apply_result(sm, client, job, model, cb)
