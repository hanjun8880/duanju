"""Pre-deduct -> settle (多退少补) / full refund ledger.

Every public function owns one transaction: it locks the user row (SELECT ... FOR UPDATE), and for job
transitions the job row first, then the user row (fixed lock order => no deadlock between settle/refund).
A job can leave queued/processing exactly once, which makes settle/refund idempotent and race-safe.
"""
import uuid
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import media
from src.config import settings
from src.models import Asset, Base, CreditLedger, Episode, Job, ModelCatalog, Project, Shot, User

Q = Decimal("0.0001")
OPEN_STATES = ("queued", "processing")
REFUND_REASONS = {"provider_5xx_refund", "provider_4xx_refund", "timeout_refund", "policy_reject_refund",
                  "cancel_refund"}


class LedgerError(Exception):
    pass


class InsufficientCredits(LedgerError):
    pass


class DailyLimitExceeded(LedgerError):
    pass


class ModelUnavailable(LedgerError):
    pass


class InvalidJobState(LedgerError):
    pass


class BudgetExceeded(LedgerError):
    """Project / episode / daily USD budget would be exceeded (message is user-facing Chinese)."""


def estimate_credits(model: ModelCatalog, billable_units: int, resolution: str) -> Decimal:
    """units = seconds for video (input+output seconds for video.edit), images for T2I."""
    mult = Decimal(str(model.resolution_multipliers.get(resolution, 1)))
    return (model.credits_per_unit * billable_units * mult).quantize(Q)


def usd_factor(model_key: str, resolution: str) -> Decimal:
    return Decimal(str(settings.usd_resolution_factors.get(model_key, {}).get(resolution, 1)))


def estimate_usd(model: ModelCatalog, billable_units: int, resolution: str) -> Decimal:
    """Reference USD cost. Models without usd_per_unit fall back to credits x settings.credit_usd."""
    if model.usd_per_unit is None:
        return (estimate_credits(model, billable_units, resolution) * Decimal(str(settings.credit_usd))).quantize(Q)
    return (model.usd_per_unit * billable_units * usd_factor(model.model_key, resolution)).quantize(Q)


def job_usd(job: Job) -> Decimal:
    """Money a job counts against budgets: settled share of its USD estimate, the full estimate while open,
    nothing once refunded."""
    if job.status not in ("success", *OPEN_STATES):
        return Decimal(0)
    raw = job.request_payload.get("usd_estimate")
    est = Decimal(raw) if raw is not None else job.credit_estimate * Decimal(str(settings.credit_usd))
    if job.status == "success" and job.actual_credits is not None and job.credit_estimate:
        return (est * job.actual_credits / job.credit_estimate).quantize(Q)
    return est


def _fmt(d: Decimal) -> str:
    return f"${d.quantize(Decimal('0.01'))}"


async def _usage(session: AsyncSession, *where: Any) -> tuple[Decimal, Decimal]:
    """(settled, in flight) USD for jobs matching where."""
    jobs = (await session.execute(select(Job).where(Job.status.in_(("success", *OPEN_STATES)), *where))).scalars()
    spent, flight = Decimal(0), Decimal(0)
    for j in jobs:
        if j.status == "success":
            spent += job_usd(j)
        else:
            flight += job_usd(j)
    return spent, flight


async def check_budget(session: AsyncSession, *, user_id: uuid.UUID, project_id: uuid.UUID,
                       episode_number: int | None, usd: Decimal) -> None:
    """Spent + in flight + this job must stay within project, episode and daily budgets."""
    if usd <= 0:
        return
    project = await session.get(Project, project_id)
    checks: list[tuple[str, Decimal, Any]] = []
    if project is not None and project.budget_usd is not None:
        checks.append(("项目", project.budget_usd, (Job.project_id == project_id,)))
    if episode_number is not None:
        ep_budget = (await session.execute(select(Episode.budget_usd).where(
            Episode.project_id == project_id, Episode.episode_number == episode_number))).scalar_one_or_none()
        if ep_budget is not None:
            in_ep = select(Shot.id).where(Shot.project_id == project_id, Shot.episode_number == episode_number)
            checks.append((f"第 {episode_number} 集", ep_budget, (Job.project_id == project_id, Job.shot_id.in_(in_ep))))
    if settings.daily_budget_usd is not None:
        mine = select(Project.id).where(Project.user_id == user_id)
        checks.append(("今日", Decimal(str(settings.daily_budget_usd)),
                       (Job.project_id.in_(mine), Job.created_at >= func.date_trunc("day", func.now()))))
    for label, budget, where in checks:
        spent, flight = await _usage(session, *where)
        if spent + flight + usd > budget:
            raise BudgetExceeded(f"{label}预算不足：预算 {_fmt(budget)}，已花 {_fmt(spent)}，进行中 {_fmt(flight)}，"
                                 f"本次约 {_fmt(usd)}。请提高预算或降低档位后再试。")


def _asset_present(key: str) -> bool:
    if key.startswith(media.PREFIX):
        return media.local_path(key) is not None
    return not key.startswith("pending/") and Path(key).is_file()


async def find_cached(session: AsyncSession, project_id: uuid.UUID, content_hash: str) -> tuple[Job, list[Asset]] | None:
    """Newest successful job in this project with the same content hash whose outputs are all still on disk."""
    jobs = (await session.execute(select(Job).where(
        Job.project_id == project_id, Job.content_hash == content_hash, Job.status == "success")
        .order_by(Job.created_at.desc()).limit(5))).scalars().all()
    for j in jobs:
        assets = list((await session.execute(select(Asset).where(Asset.job_id == j.id))).scalars())
        if assets and all(_asset_present(a.s3_key) for a in assets):
            return j, assets
    return None


async def record_cached_job(session: AsyncSession, *, source: Job, assets: Sequence[Asset], shot_id: uuid.UUID | None,
                            task_type: str, request_payload: dict[str, Any]) -> Job:
    """Zero-cost success job that reuses an earlier identical render (copies its asset rows)."""
    async with session.begin():
        job = Job(project_id=source.project_id, shot_id=shot_id, task_type=task_type, model_key=source.model_key,
                  request_payload={**request_payload, "usd_estimate": "0", "cached_from": str(source.id)},
                  credit_estimate=Decimal(0), actual_credits=Decimal(0), status="success",
                  content_hash=source.content_hash, completed_at=func.now())
        session.add(job)
        await session.flush()
        session.add_all([Asset(job_id=job.id, project_id=source.project_id, s3_key=a.s3_key, kind=a.kind,
                               meta={**a.meta, "shot_id": str(shot_id) if shot_id else None,
                                     "render_tier": request_payload.get("render_tier"), "cached_from": str(source.id)})
                         for a in assets])
    await session.refresh(job)
    return job


async def _locked_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = (await session.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one()
    return user


async def _spent_today(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    net = (await session.execute(
        select(func.coalesce(func.sum(CreditLedger.delta), 0)).where(
            CreditLedger.user_id == user_id,
            CreditLedger.reason != "manual_topup",
            CreditLedger.created_at >= func.date_trunc("day", func.now()),
        ))).scalar_one()
    return -Decimal(net)


def _post(session: AsyncSession, user: User, job_id: uuid.UUID | None, delta: Decimal, reason: str) -> None:
    user.credits = user.credits + delta
    session.add(CreditLedger(user_id=user.id, job_id=job_id, delta=delta, balance=user.credits, reason=reason))


async def create_job_with_prededuct(
    session: AsyncSession, *, user_id: uuid.UUID, project_id: uuid.UUID, model_key: str, task_type: str,
    request_payload: dict[str, Any], billable_units: int, resolution: str, shot_id: uuid.UUID | None = None,
    episode_number: int | None = None, content_hash: str | None = None,
) -> Job:
    async with session.begin():
        model = await session.get(ModelCatalog, model_key)
        if model is None or not model.is_active:
            raise ModelUnavailable(model_key)
        estimate = estimate_credits(model, billable_units, resolution)
        usd = estimate_usd(model, billable_units, resolution)
        user = await _locked_user(session, user_id)  # serialises job creation per user: budget check is race-free
        if user.credits < estimate:
            raise InsufficientCredits(f"need {estimate}, have {user.credits}")
        if await _spent_today(session, user_id) + estimate > user.daily_limit_credits:
            raise DailyLimitExceeded(f"daily limit {user.daily_limit_credits}")
        if episode_number is None and shot_id is not None:
            episode_number = (await session.execute(select(Shot.episode_number).where(Shot.id == shot_id))
                              ).scalar_one_or_none()
        await check_budget(session, user_id=user_id, project_id=project_id, episode_number=episode_number, usd=usd)
        job = Job(project_id=project_id, shot_id=shot_id, task_type=task_type, model_key=model_key,
                  request_payload={**request_payload, "usd_estimate": str(usd)}, credit_estimate=estimate,
                  status="queued", content_hash=content_hash)
        session.add(job)
        await session.flush()
        if estimate:  # zero-credit local jobs leave no ledger noise
            _post(session, user, job.id, -estimate, "pre_deduct")
    return job


async def _close_job(session: AsyncSession, job_id: uuid.UUID) -> tuple[Job, User]:
    job = (await session.execute(select(Job).where(Job.id == job_id).with_for_update())).scalar_one()
    if job.status not in OPEN_STATES:
        raise InvalidJobState(f"job {job_id} already {job.status}")
    owner = (await session.execute(select(Project.user_id).where(Project.id == job.project_id))).scalar_one()
    return job, await _locked_user(session, owner)


async def settle_job(session: AsyncSession, job_id: uuid.UUID, actual_credits: Decimal,
                     extra: Sequence[Base] = ()) -> Job:
    """extra: rows (e.g. output assets) committed atomically with the settlement."""
    actual = Decimal(actual_credits).quantize(Q)
    if actual < 0:
        raise ValueError("actual_credits must be >= 0")
    async with session.begin():
        job, user = await _close_job(session, job_id)
        diff = job.credit_estimate - actual
        if diff > 0:
            _post(session, user, job.id, diff, "settle_refund")
        elif diff < 0:
            # provider already billed us; balance may go below zero and blocks the next pre-deduct
            _post(session, user, job.id, diff, "settle_extra_charge")
        session.add_all(extra)
        job.actual_credits = actual
        job.status = "success"
        job.completed_at = func.now()
    await session.refresh(job)
    return job


async def refund_job(
    session: AsyncSession, job_id: uuid.UUID, *, reason: str, status: str = "failed", error: str | None = None,
) -> Job:
    if reason not in REFUND_REASONS:
        raise ValueError(f"unknown refund reason {reason}")
    if status not in ("failed", "dead_letter", "cancelled"):
        raise ValueError(f"bad terminal status {status}")
    async with session.begin():
        job, user = await _close_job(session, job_id)
        if job.credit_estimate:
            _post(session, user, job.id, job.credit_estimate, reason)
        job.actual_credits = Decimal(0)
        job.status = status
        job.error_message = error
        job.completed_at = func.now()
    await session.refresh(job)
    return job


async def topup(session: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> Decimal:
    if amount <= 0:
        raise ValueError("amount must be > 0")
    async with session.begin():
        user = await _locked_user(session, user_id)
        _post(session, user, None, Decimal(amount).quantize(Q), "manual_topup")
    return user.credits
