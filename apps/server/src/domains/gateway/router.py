"""Job creation, vendor dispatch with one-hop failover, and webhook settlement."""
import json
import logging
import os
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway.adapters import ADAPTERS
from src.domains.gateway.adapters.base import (
    ProviderError,
    ProviderNotConfigured,
    ProviderRejected,
    ProviderRetryable,
)
from src.domains.gateway.schemas import JobCreateRequest, preflight
from src.models import Asset, Job, ModelCatalog, Project

log = logging.getLogger(__name__)
__all__ = ["ProviderNotConfigured", "ProviderRejected", "ProviderUnavailable", "WebhookAuthError"]


class ProviderUnavailable(ProviderError):
    """Primary and fallback both failed (money already refunded)."""


class WebhookAuthError(Exception):
    pass


def webhook_url(provider: str) -> str:
    return f"{os.environ.get('PUBLIC_BASE_URL', 'http://localhost:8000').rstrip('/')}/api/v1/webhooks/{provider}"


async def create_job(session: AsyncSession, *, user_id: uuid.UUID, req: JobCreateRequest,
                     model_key: str | None = None, failover_of: uuid.UUID | None = None,
                     meta: dict[str, Any] | None = None) -> Job:
    task, canonical = preflight(req)
    payload: dict[str, Any] = {**(meta or {}), "request": req.model_dump(mode="json"), "task_type": task,
                               "canonical": canonical, "billable_units": req.billable_units(),
                               "resolution": req.resolution}
    if failover_of:
        payload["failover_of"] = str(failover_of)
    return await ledger.create_job_with_prededuct(
        session, user_id=user_id, project_id=req.project_id, model_key=model_key or req.model_key, task_type=task,
        request_payload=payload, billable_units=req.billable_units(), resolution=req.resolution, shot_id=req.shot_id)


async def _refund(sm: async_sessionmaker[AsyncSession], job_id: uuid.UUID, reason: str, error: str,
                  status: str = "failed") -> None:
    async with sm() as s:
        await ledger.refund_job(s, job_id, reason=reason, status=status, error=error)


async def _failover(sm: async_sessionmaker[AsyncSession], job: Job, model: ModelCatalog,
                    client: httpx.AsyncClient, error: str) -> Job:
    if not model.fallback_model_key or job.request_payload.get("failover_of"):
        raise ProviderUnavailable(error)  # one hop only: seedance<->wan must not ping-pong
    async with sm() as s:
        owner = (await s.execute(select(Project.user_id).where(Project.id == job.project_id))).scalar_one()
    req = JobCreateRequest.model_validate(job.request_payload["request"])
    async with sm() as s:
        meta: dict[str, Any] = {k: job.request_payload[k] for k in ("render_tier", "chain") if k in job.request_payload}
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
        raise
    except ProviderRejected as e:
        await _refund(sm, job.id, "provider_4xx_refund", str(e))
        raise
    except ProviderRetryable as e:
        await _refund(sm, job.id, "timeout_refund" if e.timeout else "provider_5xx_refund", str(e))
        return await _failover(sm, job, model, client, str(e))
    async with sm() as s, s.begin():
        locked = (await s.execute(select(Job).where(Job.id == job.id).with_for_update())).scalar_one()
        if locked.status == "queued":
            locked.status, locked.provider_job_id = "processing", pid
    async with sm() as s:
        fresh = await s.get(Job, job.id)
        assert fresh is not None
        return fresh


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
    try:
        if cb.ok:
            units = cb.billed_seconds or job.request_payload["billable_units"]
            actual = ledger.estimate_credits(model, units, job.request_payload["resolution"])
            # shortcut: s3_key is a placeholder until the post-processing queue downloads into MinIO (Phase 3)
            outputs = [Asset(job_id=job.id, project_id=job.project_id, kind=kind,
                             s3_key=f"pending/{job.id}/{kind}", meta={"source_url": url, "s3": "pending_download"})
                       for kind, url in (("video", cb.video_url), ("last_frame", cb.last_frame_url)) if url]
            async with sm() as s:
                await ledger.settle_job(s, job.id, actual, extra=outputs)
            if job.shot_id and "render_tier" in job.request_payload:
                from src.domains.storyboard.shot_orchestrator import on_shot_rendered
                async with sm() as s:
                    await on_shot_rendered(s, job.shot_id, job.request_payload["render_tier"], cb.last_frame_url,
                                           bool(job.request_payload.get("chain")))
            return {"status": "success", "job_id": str(job.id), "actual_credits": str(actual)}
        await _refund(sm, job.id, "provider_5xx_refund", cb.error or "provider reported failure")
    except ledger.InvalidJobState:
        return {"status": "duplicate", "job_id": str(job.id)}
    try:
        new = await _failover(sm, job, model, client, cb.error or "failed")
        return {"status": "failed", "job_id": str(job.id), "failover_job_model": new.model_key}
    except ProviderError as e:
        return {"status": "failed", "job_id": str(job.id), "failover_job_model": None, "error": str(e)}
