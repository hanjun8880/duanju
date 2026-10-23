"""Every LLM call leaves one jobs row (task_type llm.<stage>, zero credits, usage + USD in request_payload)."""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.config import settings
from src.domains.script import usage
from src.domains.script.script_engine import CachedDirectorLLM
from src.main import app
from tests.test_script_engine import claude_response, make_claude
from tests.test_series import NOVEL, claude  # noqa: F401  (fixture reuse)


def test_usd_from_usage_and_config_prices(monkeypatch):
    monkeypatch.setattr(settings, "llm_prices", {"m": {"input": 2.0, "output": 10.0, "cache_read": 0.2,
                                                       "cache_write": 4.0}})
    u = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 500_000,
         "cache_creation_input_tokens": 250_000}
    assert usage.usd("m", u) == pytest.approx(2.0 + 1.0 + 0.1 + 1.0)
    assert usage.usd("unknown-model", u) is None  # unpriced, never guessed


def test_default_prices_cover_routed_llm_models():
    from src.domains.gateway.routing import STAGES
    for s in STAGES.values():
        if s.kind in ("llm", "vision") and s.provider == "anthropic":
            assert s.model in settings.llm_prices, s.model


async def _llm_jobs(engine, pid):
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT task_type, model_key, credit_estimate, status, request_payload, shot_id FROM jobs "
            "WHERE project_id=:p AND task_type LIKE 'llm.%' ORDER BY created_at"), {"p": pid})).all()


async def test_each_llm_call_writes_a_job(engine, claude):  # noqa: F811
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "雾港", "source_novel_text": NOVEL})).json()["id"]
        assert (await c.post(f"/api/v1/projects/{pid}/plan", json={"episode_count": 2})).status_code == 201
        assert (await c.post(f"/api/v1/projects/{pid}/episodes/1/script")).status_code == 201
        assert (await c.post(f"/api/v1/projects/{pid}/episodes/1/storyboard")).status_code == 201
        rows = await _llm_jobs(engine, pid)
        assert [r.task_type for r in rows] == ["llm.plan", "llm.script", "llm.storyboard"]
        assert [r.model_key for r in rows] == ["llm_plan", "llm_script", "llm_storyboard"]
        assert all(r.credit_estimate == 0 and r.status == "success" and r.shot_id is None for r in rows)
        plan = rows[0].request_payload
        assert plan["stage"] == "plan" and plan["model"] == "claude-opus-5-5"
        assert plan["usage"]["cache_read_input_tokens"] == 9000 and plan["usage"]["output_tokens"] == 10
        assert plan["usd"] == pytest.approx(usage.usd("claude-opus-5-5", plan["usage"]))
        assert plan["usd"] > 0

        spend = (await c.get(f"/api/v1/projects/{pid}/llm-usage")).json()
        assert spend["calls"] == 3
        assert spend["total_usd"] == pytest.approx(sum(r.request_payload["usd"] for r in rows))
        assert set(spend["by_stage"]) == {"plan", "script", "storyboard"}
        assert spend["by_stage"]["plan"]["calls"] == 1


async def test_refused_call_is_still_ledgered(engine, monkeypatch):
    from src.domains.script import api as script_api
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(script_api, "CachedDirectorLLM",
                        lambda: CachedDirectorLLM(client=make_claude(lambda r: claude_response("", "refusal"))))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "x", "source_novel_text": "一个创意"})).json()["id"]
        assert (await c.post(f"/api/v1/projects/{pid}/plan", json={})).status_code == 422
        rows = await _llm_jobs(engine, pid)
        assert [r.task_type for r in rows] == ["llm.plan"]  # the tokens were spent even though it declined
        assert json.dumps(rows[0].request_payload)
