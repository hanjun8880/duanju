"""LLM spend ledger: one jobs row per LLM call (task_type llm.<stage>, zero credits).

The jobs.model_key FK points at llm_<stage> placeholder rows (migration 0007_script); the real model id,
token usage and USD estimate live in request_payload. Prices: settings.llm_prices (USD per million tokens,
placeholders, override with env LLM_PRICES).
"""
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import settings
from src.models import Job

LLM_STAGES = ("summarize", "plan", "script", "storyboard", "reformat", "remix_vision", "remix_rewrite")


def usd(model: str, usage: dict[str, Any]) -> float | None:
    p = settings.llm_prices.get(model)
    if p is None:
        return None
    mtok = 1_000_000
    return (usage.get("input_tokens", 0) * p.get("input", 0)
            + usage.get("output_tokens", 0) * p.get("output", 0)
            + (usage.get("cache_read_input_tokens") or 0) * p.get("cache_read", 0)
            + (usage.get("cache_creation_input_tokens") or 0) * p.get("cache_write", 0)) / mtok


async def record_llm_calls(project_id: uuid.UUID | str, calls: list[dict[str, Any]],
                           sm: async_sessionmaker[AsyncSession] | None = None) -> None:
    if not calls:
        return
    if sm is None:
        from src.db import SessionLocal
        sm = SessionLocal
    pid = uuid.UUID(str(project_id))
    batch = list(calls)
    calls.clear()  # recorded once, even if the caller records again in a finally
    async with sm() as s, s.begin():
        s.add_all([Job(project_id=pid, task_type=f"llm.{c['stage']}",
                       model_key=f"llm_{c['stage'] if c['stage'] in LLM_STAGES else 'plan'}",
                       request_payload={"stage": c["stage"], "model": c["model"], "provider": c.get("provider"),
                                        "usage": c["usage"], "usd": usd(c["model"], c["usage"])},
                       credit_estimate=0, status="success", completed_at=func.now()) for c in batch])


async def project_spend(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    rows = (await session.execute(select(Job.request_payload).where(
        Job.project_id == project_id, Job.task_type.like("llm.%")))).scalars().all()
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by.setdefault(r.get("stage", "?"), {"calls": 0, "usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                                               "unpriced_calls": 0})
        b["calls"] += 1
        b["input_tokens"] += int(r.get("usage", {}).get("input_tokens", 0))
        b["output_tokens"] += int(r.get("usage", {}).get("output_tokens", 0))
        if r.get("usd") is None:
            b["unpriced_calls"] += 1
        else:
            b["usd"] += float(r["usd"])
    return {"calls": len(rows), "total_usd": sum(b["usd"] for b in by.values()), "by_stage": by}
