"""Pre-deduct -> settle (多退少补) / full refund ledger.

Every public function owns one transaction: it locks the user row (SELECT ... FOR UPDATE), and for job
transitions the job row first, then the user row (fixed lock order => no deadlock between settle/refund).
A job can leave queued/processing exactly once, which makes settle/refund idempotent and race-safe.
"""
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Base, CreditLedger, Job, ModelCatalog, Project, User

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


def estimate_credits(model: ModelCatalog, billable_units: int, resolution: str) -> Decimal:
    """units = seconds for video (input+output seconds for video.edit), images for T2I."""
    mult = Decimal(str(model.resolution_multipliers.get(resolution, 1)))
    return (model.credits_per_unit * billable_units * mult).quantize(Q)


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
) -> Job:
    async with session.begin():
        model = await session.get(ModelCatalog, model_key)
        if model is None or not model.is_active:
            raise ModelUnavailable(model_key)
        estimate = estimate_credits(model, billable_units, resolution)
        user = await _locked_user(session, user_id)
        if user.credits < estimate:
            raise InsufficientCredits(f"need {estimate}, have {user.credits}")
        if await _spent_today(session, user_id) + estimate > user.daily_limit_credits:
            raise DailyLimitExceeded(f"daily limit {user.daily_limit_credits}")
        job = Job(project_id=project_id, shot_id=shot_id, task_type=task_type, model_key=model_key,
                  request_payload=request_payload, credit_estimate=estimate, status="queued")
        session.add(job)
        await session.flush()
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
