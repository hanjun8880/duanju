"""Long novels: summarize (4 segments per call, cached by segment sha256) -> plan the bible -> outline in
batches of <=20 episodes. Real API, real Postgres, real Anthropic SDK; only Claude's transport is stubbed."""
import asyncio
import json
import re

import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.domains.gateway.routing import model_for
from src.domains.script import api as script_api
from src.domains.script import series
from src.domains.script.script_engine import CachedDirectorLLM, SeriesPlan, bible_text
from src.main import app
from tests.test_script_engine import claude_response, make_claude

CHAPTER_BODY = "沈舟在旧城巷口守着一盏灯，等一个不会回来的人。" * 110  # ~2500 chars


def novel(n: int = 20, changed: int | None = None) -> str:
    return "\n".join(f"第{i}章 灯\n" + (("改写过的一章。" * 300) if i == changed else CHAPTER_BODY + str(i))
                     for i in range(1, n + 1))


SETTING = {"title": "旧城灯", "logline": "守灯人", "episode_count": 7,
           "characters": [{"name": "沈舟", "role_tag": "主角", "visual_prompt": "三十岁", "personality": "沉默"}],
           "props": [{"name": "灯", "visual_prompt": "铜灯"}], "scenes": [{"name": "巷口", "visual_prompt": "雨夜"}]}


@pytest.fixture
def stub(monkeypatch):
    log: dict = {"summarize": [], "setting": [], "outline": [], "inflight": 0, "max_inflight": 0}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        schema = json.dumps(body["output_config"]["format"]["schema"])
        user = body["messages"][0]["content"]
        if '"digests"' in schema:
            log["summarize"].append(body)
            log["inflight"] += 1
            log["max_inflight"] = max(log["max_inflight"], log["inflight"])
            await asyncio.sleep(0.02)
            log["inflight"] -= 1
            nums = [int(x) for x in re.findall(r"【片段 (\d+)】", user)]
            out = {"digests": [{"segment": i, "summary": f"摘要{i}", "characters": ["沈舟"], "scenes": ["巷口"],
                                "props": ["灯"]} for i in nums]}
        elif '"episode_count"' in schema:
            log["setting"].append(body)
            out = SETTING
        else:
            log["outline"].append(body)
            a, b = (int(x) for x in re.search(r"第 (\d+)-(\d+) 集", user).groups())
            out = {"episodes": [{"episode_number": i, "title": f"集{i}", "synopsis": f"梗概{i}",
                                 "source_segments": [i, 999]} for i in range(a, b + 1)]}
        return claude_response(json.dumps(out, ensure_ascii=False))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(script_api, "CachedDirectorLLM", lambda: CachedDirectorLLM(client=make_claude(handler)))
    return log


def test_limits_and_oversized_chapters():
    assert series.MAX_SOURCE_CHARS == 2_000_000
    huge = "第一章 甲\n" + ("很长的一段。\n" * 4000) + "第二章 乙\n短。"
    parts = series.split_source(huge)
    assert len(parts) > 2 and all(len(p) <= series.MAX_SEGMENT + 200 for p in parts)
    assert parts[-1].startswith("第二章")


def test_bookkeeping_keys_never_reach_the_prompt():
    plan = SeriesPlan(title="t", logline="l", characters=[], props=[], scenes=[], episodes=[])
    b = series.bible_from_plan(plan, {"_cache": {"h": {"summary": "x"}}, "_progress": {}, "title": "old"})
    assert b["_cache"] == {"h": {"summary": "x"}} and b["title"] == "t"
    assert "_cache" not in bible_text(b) and "_progress" not in bible_text(b)
    assert bible_text(b) == bible_text(series.bible_from_plan(plan))


async def test_long_novel_two_stage_plan(engine, stub):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "灯", "source_novel_text": novel()})).json()["id"]
        assert (await c.get(f"/api/v1/projects/{pid}/plan/progress")).json()["stage"] == "idle"
        r = await c.post(f"/api/v1/projects/{pid}/plan", json={"episode_count": 25})
        assert r.status_code == 201, r.text
        assert r.json()["episodes"] == 25 and r.json()["title"] == "旧城灯"

        assert len(stub["summarize"]) == 5  # 20 segments, 4 per call
        assert 1 < stub["max_inflight"] <= 3  # concurrent, capped at 3
        assert all(b["model"] == model_for("summarize").model for b in stub["summarize"])
        assert len(stub["setting"]) == 1 and stub["setting"][0]["model"] == model_for("plan").model
        assert "摘要20" in stub["setting"][0]["messages"][0]["content"]
        assert len(stub["outline"]) == 2  # 20 + 5 episodes
        # the outline batches share one byte-stable cached system prefix (rules + digests, then the bible)
        assert stub["outline"][0]["system"] == stub["outline"][1]["system"]
        assert '"沈舟"' in stub["outline"][0]["system"][1]["text"]

        eps = (await c.get(f"/api/v1/projects/{pid}/episodes")).json()
        assert [e["episode_number"] for e in eps] == list(range(1, 26))
        assert eps[0]["source_segments"] == [1]  # out-of-range segment dropped
        prog = (await c.get(f"/api/v1/projects/{pid}/plan/progress")).json()
        assert prog["stage"] == "done" and prog["episodes"] == 25
        project = (await c.get(f"/api/v1/projects/{pid}")).json()
        assert "_cache" not in project["series_bible_json"]  # bookkeeping stays out of the API view
        async with engine.connect() as conn:
            bible = (await conn.execute(text("SELECT series_bible_json FROM projects WHERE id=:p"),
                                        {"p": pid})).scalar_one()
            kinds = (await conn.execute(text("SELECT task_type, count(*) FROM jobs WHERE project_id=:p "
                                             "GROUP BY task_type"), {"p": pid})).all()
        assert len(bible["_cache"]) == 20 and bible["characters"][0]["name"] == "沈舟"
        assert dict(kinds) == {"llm.summarize": 5, "llm.plan": 3}

        # re-plan: digests come from the cache; one rewritten chapter costs one small summarize call
        await c.post(f"/api/v1/projects/{pid}/plan", json={"episode_count": 7, "replace": True})
        assert len(stub["summarize"]) == 5
        await c.patch(f"/api/v1/projects/{pid}", json={"source_novel_text": novel(changed=3)})
        r = await c.post(f"/api/v1/projects/{pid}/plan", json={"replace": True})
        assert r.status_code == 201 and r.json()["episodes"] == 7  # count from the setting call
        assert len(stub["summarize"]) == 6
        assert re.findall(r"【片段 (\d+)】", stub["summarize"][-1]["messages"][0]["content"]) == ["3"]


async def test_short_source_stays_single_call(engine, stub, monkeypatch):
    calls: list[dict] = []

    def handler(request):
        calls.append(json.loads(request.content))
        return claude_response(json.dumps({**SETTING, "episodes": [
            {"episode_number": 1, "title": "一", "synopsis": "s", "source_segments": []}]}, ensure_ascii=False))

    monkeypatch.setattr(script_api, "CachedDirectorLLM", lambda: CachedDirectorLLM(client=make_claude(handler)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "x", "source_novel_text": "一个创意"})).json()["id"]
        assert (await c.post(f"/api/v1/projects/{pid}/plan", json={})).status_code == 201
        assert len(calls) == 1
        assert (await c.get(f"/api/v1/projects/{pid}/plan/progress")).json()["stage"] == "done"


async def test_failed_plan_reports_error_progress(engine, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(script_api, "CachedDirectorLLM",
                        lambda: CachedDirectorLLM(client=make_claude(lambda r: claude_response("", "max_tokens"))))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "x", "source_novel_text": novel()})).json()["id"]
        assert (await c.post(f"/api/v1/projects/{pid}/plan", json={})).status_code == 422
        prog = (await c.get(f"/api/v1/projects/{pid}/plan/progress")).json()
        assert prog["stage"] == "error" and "truncated" in prog["error"]
