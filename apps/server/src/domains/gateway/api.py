import logging
import os
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import settings
from src.db import SessionLocal
from src.deps import Http, Session, director
from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway import router, routing
from src.domains.gateway.adapters.base import ProviderError
from src.domains.gateway.safety import ContentBlocked, check_prompt
from src.domains.gateway.schemas import JobCreateRequest
from src.models import CreditLedger, Episode, Job, ModelCatalog, Project, Shot, User

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api/v1")


def model_view(m: ModelCatalog) -> dict[str, Any]:
    # provider name is shown (useful for the admin page); api_base/keys never are.
    return {"model_key": m.model_key, "display_name": m.display_name, "task_type": m.task_type,
            "provider": m.provider,
            "is_nsfw": m.is_nsfw, "is_active": m.is_active, "credits_per_unit": str(m.credits_per_unit),
            "max_duration": m.max_duration, "max_refs": m.max_refs, "fallback_model_key": m.fallback_model_key,
            "resolution_multipliers": m.resolution_multipliers, "health_score": str(m.health_score),
            "usd_per_unit": None if m.usd_per_unit is None else str(m.usd_per_unit),
            "usd_resolution_factors": settings.usd_resolution_factors.get(m.model_key, {})}


def job_view(j: Job) -> dict[str, Any]:
    return {"id": str(j.id), "shot_id": str(j.shot_id) if j.shot_id else None, "project_id": str(j.project_id),
            "status": j.status, "model_key": j.model_key, "task_type": j.task_type,
            "credit_estimate": str(j.credit_estimate),
            "actual_credits": None if j.actual_credits is None else str(j.actual_credits),
            "error_message": j.error_message, "created_at": j.created_at.isoformat(),
            "usd": str(ledger.job_usd(j)), "stage": j.request_payload.get("stage"),
            "cached_from": j.request_payload.get("cached_from")}


@api.get("/models")
async def list_models(session: Session) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(ModelCatalog).where(ModelCatalog.is_active).order_by(ModelCatalog.model_key))).scalars()
    return [{k: v for k, v in model_view(m).items() if k != "provider"} for m in rows]


def _task_fits_model(task: str, model_task: str) -> bool:
    family = model_task.split(".")[0]
    return task.startswith(family + ".") if family in ("video", "image") else task == model_task


async def _dispatch_bg(job_id: uuid.UUID, client: httpx.AsyncClient) -> None:
    try:
        await router.dispatch_job(SessionLocal, job_id, client)
    except ProviderError as e:  # job row already refunded / marked
        log.warning("dispatch %s failed: %s", job_id, e)


async def submit(req: JobCreateRequest, session: AsyncSession, bg: BackgroundTasks, http: httpx.AsyncClient,
                 meta: dict[str, Any] | None = None, force: bool = False) -> Job:
    """All generation goes through here: gates, limits, budgets, cache, pre-deduct, then queue.
    force=True skips the content-hash cache (a deliberate re-roll)."""
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
        job = await router.create_job(session, user_id=user.id, req=req, meta=meta, force=force)
    except (ledger.InsufficientCredits, ledger.DailyLimitExceeded, ledger.BudgetExceeded) as e:
        raise HTTPException(402, str(e)) from e
    except ledger.ModelUnavailable as e:
        raise HTTPException(422, f"model {e} unavailable") from e
    from src.domains.storyboard.shot_orchestrator import enqueue_local
    from src.queue import celery_app, queue_for
    if job.status == "success":  # content-hash cache hit: nothing to pay or dispatch, run the follow-ups now
        await session.close()
        await router.run_cached_followups(SessionLocal, http, job)
    elif model.provider == "local":
        enqueue_local(job.task_type, str(job.id))
    elif settings.dispatch_mode == "inline":
        bg.add_task(_dispatch_bg, job.id, http)
    else:
        celery_app.send_task("gateway.dispatch", args=[str(job.id)],
                             queue=queue_for(job.task_type, job.model_key, model.provider))
    return job


@api.post("/jobs", status_code=201)
async def create_job(req: JobCreateRequest, session: Session, bg: BackgroundTasks, http: Http,
                     force: bool = False) -> dict[str, Any]:
    return job_view(await submit(req, session, bg, http, force=force))


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
    usd_per_unit: Decimal | None = None
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
        if k in ("credits_per_unit", "usd_per_unit") and v is not None and v < 0:
            raise HTTPException(422, "price must be >= 0")
        setattr(m, k, v)
    await session.commit()
    await session.refresh(m)
    return model_view(m)


def _vendor_rows() -> list[dict[str, Any]]:
    """Which vendor API keys are configured, never the keys themselves. Pulled from the three places
    credentials are checked: gateway adapters (HttpAdapter env_prefix + ArkAdapter's own), the text-vendor
    table in script_engine, and voice/providers.py's TTS credentials."""
    from src.domains.gateway.adapters import ADAPTERS
    from src.domains.gateway.adapters.ark import ArkAdapter
    from src.domains.script.script_engine import VENDOR_API_KEY_ENV
    from src.domains.voice.providers import CREDENTIAL_ENV

    rows: dict[str, dict[str, Any]] = {}

    def add(provider: str, kind: str, key_envs: tuple[str, ...]) -> None:
        row = rows.setdefault(provider, {"provider": provider, "kinds": [], "key_envs": list(key_envs),
                                         "key_configured": bool(key_envs) and all(os.environ.get(k) for k in key_envs)})
        if kind not in row["kinds"]:
            row["kinds"].append(kind)

    add("anthropic", "text", ("ANTHROPIC_API_KEY",))
    for name, adapter in ADAPTERS.items():
        key_envs = ("ARK_API_KEY",) if isinstance(adapter, ArkAdapter) else (f"{name.upper()}_API_KEY",)
        add(name, "image/video", key_envs)
    for provider, env in VENDOR_API_KEY_ENV.items():
        add(provider, "text", (env,))
    for provider, envs in CREDENTIAL_ENV.items():
        if provider != "local":
            add(provider, "tts", envs)
    return sorted(rows.values(), key=lambda r: str(r["provider"]))


@api.get("/admin/providers")
async def admin_providers() -> list[dict[str, Any]]:
    return _vendor_rows()


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


# ---- per-stage model assignment (persisted overrides on top of gateway/routing.STAGES) ----

def stage_view(stage: str) -> dict[str, Any]:
    eff, base = routing.model_for(stage), routing.STAGES[stage]
    return {"stage": stage, "kind": eff.kind, "model": eff.model, "provider": eff.provider,
            "resolution": eff.resolution, "note": eff.note, "overridden": eff != base,
            "default": {"model": base.model, "provider": base.provider, "resolution": base.resolution}}


async def load_stage_overrides(sm: async_sessionmaker[AsyncSession]) -> int:
    """Startup: re-apply admin overrides saved in stage_overrides. Returns how many were applied."""
    try:
        async with sm() as s:
            rows = (await s.execute(text("SELECT stage, model, provider, resolution, note FROM stage_overrides"))).all()
    except DBAPIError as e:  # table not migrated yet: run with the code defaults
        log.warning("stage overrides not loaded: %s", e)
        return 0
    n = 0
    for stage, model, provider, resolution, note in rows:
        if stage in routing.STAGES:
            routing.set_override(stage, model=model, provider=provider or "", resolution=resolution or "",
                                 note=note or "")
            n += 1
    return n


class StageUpdate(BaseModel):
    stage: str
    model: str | None = Field(default=None, description="empty/null resets the stage to its code default")
    provider: str | None = None
    resolution: Literal["480p", "720p", "1080p"] | None = None
    note: str | None = None


@api.get("/admin/stages")
async def admin_stages() -> list[dict[str, Any]]:
    return [stage_view(s) for s in routing.STAGES]


@api.put("/admin/stages")
async def put_stage(body: StageUpdate, session: Session) -> dict[str, Any]:
    if body.stage not in routing.STAGES:
        raise HTTPException(404, f"未知环节 {body.stage}")
    base = routing.STAGES[body.stage]
    if not body.model:
        await session.execute(text("DELETE FROM stage_overrides WHERE stage=:s"), {"s": body.stage})
        await session.commit()
        routing.set_override(body.stage)
        return stage_view(body.stage)
    provider = body.provider
    if base.kind in ("image", "video"):
        m = await session.get(ModelCatalog, body.model)
        if m is None or not m.task_type.startswith(base.kind + "."):
            raise HTTPException(422, f"{body.model} 不是可用的{'图片' if base.kind == 'image' else '视频'}模型")
        provider = provider or m.provider
    if body.resolution and base.kind != "video":
        raise HTTPException(422, "只有视频环节可以设置分辨率")
    await session.execute(text(
        "INSERT INTO stage_overrides (stage, model, provider, resolution, note) VALUES (:s, :m, :p, :r, :n) "
        "ON CONFLICT (stage) DO UPDATE SET model=:m, provider=:p, resolution=:r, note=:n, updated_at=now()"),
        {"s": body.stage, "m": body.model, "p": provider, "r": body.resolution, "n": body.note})
    await session.commit()
    routing.set_override(body.stage, model=body.model, provider=provider or "", resolution=body.resolution or "",
                         note=body.note or "")
    return stage_view(body.stage)


# ---- money views ----

def _shot_stage(kind: str, tier: str, final: bool) -> str | None:
    if tier == "L0_animatic":
        return None
    return routing.video_stage_for(kind, final=final, draft=tier == "L1_draft")


@api.get("/projects/{project_id}/episodes/{n}/estimate")
async def estimate_episode(project_id: uuid.UUID, n: int, session: Session,
                           tier: Literal["L0_animatic", "L1_draft", "L2_master"] = "L2_master",
                           final: bool = False) -> dict[str, Any]:
    """What rendering this episode would cost with the current stage routing (credits and reference USD).
    Keyframes: one image for every shot without a ready/approved keyframe."""
    shots = (await session.execute(select(Shot).where(Shot.project_id == project_id, Shot.episode_number == n)
                                   .order_by(Shot.shot_order))).scalars().all()
    if not shots:
        raise HTTPException(404, f"第 {n} 集还没有镜头")
    catalog = {m.model_key: m for m in (await session.execute(select(ModelCatalog))).scalars()}
    rows, credits, usd = [], Decimal(0), Decimal(0)
    for s in shots:
        stage = _shot_stage(s.shot_kind, tier, final)
        sm = routing.model_for(stage) if stage else None
        key = sm.model if sm else "local_animatic"
        res = (sm.resolution if sm and sm.resolution else s.resolution)
        m = catalog.get(key)
        units = min(max(s.duration, 4), 30)
        c = ledger.estimate_credits(m, units, res) if m else None
        u = ledger.estimate_usd(m, units, res) if m else None
        credits += c or 0
        usd += u or 0
        rows.append({"shot_id": str(s.id), "shot_order": s.shot_order, "shot_kind": s.shot_kind, "stage": stage,
                     "model_key": key, "resolution": res, "seconds": units,
                     "credits": None if c is None else str(c), "usd": None if u is None else str(u)})
    kf = routing.model_for("keyframe_image")
    kf_model = catalog.get(kf.model)
    need = [s for s in shots if s.keyframe_status not in ("ready", "approved")]
    kf_usd = ledger.estimate_usd(kf_model, 1, "720p") * len(need) if kf_model else Decimal(0)
    ep = (await session.execute(select(Episode).where(Episode.project_id == project_id, Episode.episode_number == n))
          ).scalar_one_or_none()
    return {"episode_number": n, "tier": tier, "final": final, "shots": rows, "credits": str(credits),
            "usd": str(usd), "keyframes": {"count": len(need), "model_key": kf.model, "usd": str(kf_usd)},
            "total_usd": str(usd + kf_usd), "credit_usd": str(settings.credit_usd),
            "budget_usd": None if ep is None or ep.budget_usd is None else str(ep.budget_usd)}


def _job_stage(j: Job) -> str:
    st = j.request_payload.get("stage")
    if j.task_type.startswith("llm."):
        return str(st or j.task_type.removeprefix("llm."))
    return str(st) if st else ("post" if j.task_type.startswith("post.") else j.task_type.split(".")[0])


PHASES = {"summarize": "script", "plan": "script", "script": "script", "storyboard": "script", "reformat": "script",
          "remix_vision": "remix", "remix_rewrite": "remix", "lookdev_image": "lookdev", "keyframe_image": "keyframe",
          "tts": "voice", "post": "post"}


def _phase(stage: str, task_type: str) -> str:
    """Coarse pipeline phase for the cost summary: 编剧 script / 二创 remix / lookdev / keyframe / video / voice / post."""
    if stage in PHASES:
        return PHASES[stage]
    if task_type.startswith("llm."):
        return "remix" if stage.startswith("remix") else "script"
    return "video" if stage.startswith("video") or task_type.startswith("video.") else stage


@api.get("/projects/{project_id}/costs")
async def project_costs(project_id: uuid.UUID, session: Session) -> dict[str, Any]:
    """Reference USD per pipeline stage and per episode: settled + in flight (refunded jobs count 0)."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    rows = (await session.execute(select(Job, Shot.episode_number).outerjoin(Shot, Shot.id == Job.shot_id)
                                  .where(Job.project_id == project_id))).all()
    stages: dict[str, dict[str, Any]] = {}
    phases: dict[str, Decimal] = {}
    episodes: dict[int, Decimal] = {}
    spent = flight = Decimal(0)
    for j, ep in rows:
        u = ledger.job_usd(j)
        st = _job_stage(j)
        b = stages.setdefault(st, {"phase": _phase(st, j.task_type), "jobs": 0, "usd": Decimal(0),
                                   "in_flight_usd": Decimal(0), "cached": 0, "failed": 0})
        phases[b["phase"]] = phases.get(b["phase"], Decimal(0)) + u
        b["jobs"] += 1
        b["cached"] += bool(j.request_payload.get("cached_from"))
        b["failed"] += j.status in ("failed", "dead_letter", "cancelled")
        if j.status in ledger.OPEN_STATES:
            b["in_flight_usd"] += u
            flight += u
        else:
            b["usd"] += u
            spent += u
        if ep is not None:
            episodes[ep] = episodes.get(ep, Decimal(0)) + u
    return {"project_id": str(project_id), "spent_usd": str(spent), "in_flight_usd": str(flight),
            "total_usd": str(spent + flight),
            "budget_usd": None if project.budget_usd is None else str(project.budget_usd),
            "by_stage": [{"stage": k, **{f: str(v) if isinstance(v, Decimal) else v for f, v in b.items()}}
                         for k, b in sorted(stages.items(), key=lambda kv: -(kv[1]["usd"] + kv[1]["in_flight_usd"]))],
            "by_phase": [{"phase": k, "usd": str(v)} for k, v in sorted(phases.items(), key=lambda kv: -kv[1])],
            "by_episode": [{"episode_number": k, "usd": str(v)} for k, v in sorted(episodes.items())]}


class BudgetPatch(BaseModel):
    budget_usd: Decimal | None = Field(default=None, ge=0, description="null removes the cap")


@api.put("/projects/{project_id}/budget")
async def set_project_budget(project_id: uuid.UUID, body: BudgetPatch, session: Session) -> dict[str, Any]:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    p.budget_usd = body.budget_usd
    await session.commit()
    await session.refresh(p)
    return {"project_id": str(project_id), "budget_usd": None if p.budget_usd is None else str(p.budget_usd)}


@api.put("/projects/{project_id}/episodes/{n}/budget")
async def set_episode_budget(project_id: uuid.UUID, n: int, body: BudgetPatch, session: Session) -> dict[str, Any]:
    ep = (await session.execute(select(Episode).where(Episode.project_id == project_id, Episode.episode_number == n))
          ).scalar_one_or_none()
    if ep is None:
        raise HTTPException(404, f"第 {n} 集不存在")
    ep.budget_usd = body.budget_usd
    await session.commit()
    await session.refresh(ep)
    return {"episode_number": n, "budget_usd": None if ep.budget_usd is None else str(ep.budget_usd)}
