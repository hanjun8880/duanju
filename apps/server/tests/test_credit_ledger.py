import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import text

from src.domains.gateway import credit_ledger as ledger
from tests.conftest import balance

PAYLOAD = {"prompt": "x"}


async def new_job(sm, uid, pid, *, seconds=5, resolution="480p", model="seedance_2_5_spicy"):
    async with sm() as s:
        return await ledger.create_job_with_prededuct(
            s, user_id=uid, project_id=pid, model_key=model, task_type="video.i2v",
            request_payload=PAYLOAD, billable_units=seconds, resolution=resolution)


async def ledger_rows(engine, uid):
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT delta, balance, reason FROM credit_ledger WHERE user_id=:u ORDER BY created_at, balance DESC"),
            {"u": uid})).all()


async def test_estimate_uses_unit_price_and_resolution_multiplier(sessionmaker):
    async with sessionmaker() as s:
        model = await s.get(ledger.ModelCatalog, "seedance_2_5_spicy")
        assert model is not None
        est = ledger.estimate_credits(model, 10, "1080p")
    assert est == (model.credits_per_unit * 10 * Decimal(str(model.resolution_multipliers["1080p"]))).quantize(
        Decimal("0.0001"))


async def test_prededuct_creates_job_and_ledger_row(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    job = await new_job(sessionmaker, uid, pid)
    assert job.status == "queued" and job.credit_estimate > 0
    assert await balance(engine, uid) == Decimal(100) - job.credit_estimate
    rows = await ledger_rows(engine, uid)
    assert [(r.delta, r.reason) for r in rows] == [(-job.credit_estimate, "pre_deduct")]
    assert rows[0].balance == Decimal(100) - job.credit_estimate


async def test_prededuct_rejects_insufficient_balance(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET credits=1, daily_limit_credits=1000 WHERE id=:u"), {"u": uid})
    with pytest.raises(ledger.InsufficientCredits):
        await new_job(sessionmaker, uid, pid, seconds=30, resolution="1080p")
    assert await balance(engine, uid) == Decimal(1)
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM jobs WHERE project_id=:p"), {"p": pid})).scalar() == 0


async def test_prededuct_rejects_daily_limit(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET credits=100000, daily_limit_credits=1 WHERE id=:u"), {"u": uid})
    with pytest.raises(ledger.DailyLimitExceeded):
        await new_job(sessionmaker, uid, pid, seconds=30, resolution="1080p")


async def test_concurrent_prededuct_row_lock_never_overdraws(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    async with sessionmaker() as s:
        model = await s.get(ledger.ModelCatalog, "seedance_2_5_spicy")
        assert model is not None
        one = ledger.estimate_credits(model, 5, "480p")
    # balance fits exactly 3 jobs; fire 8 concurrently
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET credits=:c, daily_limit_credits=100000 WHERE id=:u"),
                           {"c": one * 3, "u": uid})
    results = await asyncio.gather(*(new_job(sessionmaker, uid, pid) for _ in range(8)), return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 3
    assert all(isinstance(e, ledger.InsufficientCredits) for e in errs)
    assert await balance(engine, uid) == 0
    rows = await ledger_rows(engine, uid)
    assert sorted(r.balance for r in rows) == [0, one, one * 2]


async def test_settle_refunds_difference(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    job = await new_job(sessionmaker, uid, pid, seconds=10)
    actual = job.credit_estimate / 2
    async with sessionmaker() as s:
        done = await ledger.settle_job(s, job.id, actual)
    assert done.status == "success" and done.actual_credits == actual and done.completed_at is not None
    assert await balance(engine, uid) == Decimal(100) - actual
    assert (await ledger_rows(engine, uid))[-1].reason == "settle_refund"


async def test_settle_charges_extra_when_actual_exceeds_estimate(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    job = await new_job(sessionmaker, uid, pid)
    actual = job.credit_estimate + Decimal("1.5")
    async with sessionmaker() as s:
        await ledger.settle_job(s, job.id, actual)
    assert await balance(engine, uid) == Decimal(100) - actual
    assert (await ledger_rows(engine, uid))[-1].reason == "settle_extra_charge"


async def test_settle_exact_writes_no_extra_row(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    job = await new_job(sessionmaker, uid, pid)
    async with sessionmaker() as s:
        await ledger.settle_job(s, job.id, job.credit_estimate)
    assert len(await ledger_rows(engine, uid)) == 1


async def test_5xx_full_refund_and_idempotency(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    job = await new_job(sessionmaker, uid, pid, seconds=20, resolution="720p")
    async with sessionmaker() as s:
        failed = await ledger.refund_job(s, job.id, reason="provider_5xx_refund", error="HTTP 503")
    assert failed.status == "failed" and failed.error_message == "HTTP 503"
    assert await balance(engine, uid) == Decimal(100)
    assert (await ledger_rows(engine, uid))[-1].reason == "provider_5xx_refund"
    # second refund or a late settle must not move money again
    for call in (lambda s: ledger.refund_job(s, job.id, reason="provider_5xx_refund"),
                 lambda s: ledger.settle_job(s, job.id, Decimal(1))):
        async with sessionmaker() as s:
            with pytest.raises(ledger.InvalidJobState):
                await call(s)
    assert await balance(engine, uid) == Decimal(100)


async def test_concurrent_settle_and_refund_only_one_wins(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    job = await new_job(sessionmaker, uid, pid)

    async def settle():
        async with sessionmaker() as s:
            return await ledger.settle_job(s, job.id, job.credit_estimate)

    async def refund():
        async with sessionmaker() as s:
            return await ledger.refund_job(s, job.id, reason="timeout_refund", status="dead_letter")

    results = await asyncio.gather(settle(), refund(), return_exceptions=True)
    assert sum(isinstance(r, ledger.InvalidJobState) for r in results) == 1
    bal = await balance(engine, uid)
    assert bal in (Decimal(100), Decimal(100) - job.credit_estimate)


async def test_refund_counts_back_against_daily_usage(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    async with sessionmaker() as s:
        model = await s.get(ledger.ModelCatalog, "seedance_2_5_spicy")
        assert model is not None
        one = ledger.estimate_credits(model, 5, "480p")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET credits=100000, daily_limit_credits=:d WHERE id=:u"),
                           {"d": one, "u": uid})
    job = await new_job(sessionmaker, uid, pid)
    with pytest.raises(ledger.DailyLimitExceeded):
        await new_job(sessionmaker, uid, pid)
    async with sessionmaker() as s:
        await ledger.refund_job(s, job.id, reason="provider_5xx_refund")
    await new_job(sessionmaker, uid, pid)  # quota freed by the refund


async def test_unknown_or_inactive_model_rejected(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    with pytest.raises(ledger.ModelUnavailable):
        await new_job(sessionmaker, uid, pid, model="no_such_model")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE model_catalog SET is_active=false WHERE model_key='minimax_h3'"))
    try:
        with pytest.raises(ledger.ModelUnavailable):
            await new_job(sessionmaker, uid, pid, model="minimax_h3")
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE model_catalog SET is_active=true WHERE model_key='minimax_h3'"))
