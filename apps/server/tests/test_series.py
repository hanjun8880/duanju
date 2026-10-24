"""Novel/idea -> plan -> script -> storyboard through the real API, real Postgres and the real Anthropic SDK.
Only Claude's HTTP transport is stubbed; the stub answers by which structured-output schema was requested."""
import json
import uuid

import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.domains.script import api as script_api
from src.domains.script import series
from src.domains.script.script_engine import CachedDirectorLLM
from src.main import app
from tests.test_script_engine import STORYBOARD, claude_response, make_claude

NOVEL = "\n".join(f"第{n}章 雾港\n" + f"林晚在雾港码头等一封信。第{n}章的事。" * 20 for n in "一二三四")
PLAN = {"title": "雾港来信", "logline": "一封迟到二十年的信", "characters": [
    {"name": "林", "role_tag": "主角", "visual_prompt": "二十七岁，短发，墨绿风衣", "personality": "倔强"}],
    "props": [{"name": "怀表", "visual_prompt": "黄铜怀表，表盖有划痕"}],
    "scenes": [{"name": "天台", "visual_prompt": "老楼天台，晨雾"}],
    "episodes": [{"episode_number": 1, "title": "信", "synopsis": "林收到信", "source_segments": [1, 2]},
                 {"episode_number": 2, "title": "表", "synopsis": "怀表停了", "source_segments": [3, 4, 99]}]}
SCRIPT = {"episode_title": "信", "script_text": "场1 日 外 天台\n林：你终于来了。"}


@pytest.fixture
def claude(monkeypatch):
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append(body)
        props = json.dumps(body["output_config"]["format"]["schema"])
        out = PLAN if '"episodes"' in props else SCRIPT if '"script_text"' in props else STORYBOARD
        return claude_response(json.dumps(out, ensure_ascii=False))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(script_api, "CachedDirectorLLM", lambda: CachedDirectorLLM(client=make_claude(handler)))
    return seen


def test_split_source_by_chapter_and_by_size():
    parts = series.split_source(NOVEL)
    assert len(parts) == 4 and parts[0].startswith("第一章") and parts[3].startswith("第四章")
    plain = "\n".join("一段没有章节标题的文字。" * 30 for _ in range(30))
    chunks = series.split_source(plain)
    assert len(chunks) > 1 and all(len(c) <= series.CHUNK + 400 for c in chunks)
    assert series.split_source("  ") == []
    with pytest.raises(ValueError, match="分卷"):
        series.split_source("字" * (series.MAX_SOURCE_CHARS + 1))


def test_plan_prompt_idea_vs_novel():
    assert series.plan_prompt(["一个外卖员发现自己能听见城市的心跳"], 12).startswith("原创创意（共 12 集）")
    p = series.plan_prompt(series.split_source(NOVEL), None)
    assert "【片段 1】" in p and "【片段 4】" in p


async def test_novel_to_storyboard_end_to_end(engine, claude):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "雾港", "source_novel_text": NOVEL})).json()["id"]
        r = await c.post(f"/api/v1/projects/{pid}/plan", json={"episode_count": 2})
        assert r.status_code == 201, r.text
        assert r.json() == {"title": "雾港来信", "logline": "一封迟到二十年的信", "episodes": 2, "characters": 1,
                            "props": 1, "scenes": 1}
        assert (await c.post(f"/api/v1/projects/{pid}/plan", json={})).status_code == 409  # no silent re-plan
        eps = (await c.get(f"/api/v1/projects/{pid}/episodes")).json()
        assert [e["source_segments"] for e in eps] == [[1, 2], [3, 4]]  # out-of-range 99 dropped
        cards = (await c.get(f"/api/v1/projects/{pid}/lookdev")).json()
        assert {(x["asset_type"], x["name"]) for x in cards} == {("character", "林"), ("prop", "怀表"),
                                                                 ("scene", "天台")}
        project = (await c.get(f"/api/v1/projects/{pid}")).json()
        assert project["series_bible_json"]["characters"][0]["name"] == "林" and project["status"] == "planned"

        r = await c.post(f"/api/v1/projects/{pid}/episodes/1/script")
        assert r.status_code == 201 and r.json()["script_text"].startswith("场1")
        script_req = claude[-1]
        user = script_req["messages"][0]["content"]
        assert "【片段 1】" in user and "【片段 2】" in user and "【片段 3】" not in user  # only the adapted text
        assert "SERIES_BIBLE_LOCKED_ASSETS" in script_req["system"][1]["text"] and '"林"' in script_req["system"][1]["text"]
        assert script_req["stream"] is True

        r = await c.post(f"/api/v1/projects/{pid}/episodes/1/storyboard")
        assert r.status_code == 201 and r.json()["created"] == 1
        assert "场1 日 外 天台" in claude[-1]["messages"][0]["content"]  # storyboard reads the screenplay
        r = await c.post(f"/api/v1/projects/{pid}/episodes/1/storyboard")  # re-run replaces, not duplicates
        assert r.status_code == 201
        shots = (await c.get(f"/api/v1/projects/{pid}/shots", params={"episode": 1})).json()
        assert len(shots) == 1 and shots[0]["refs"]["assets"] == []  # no reference images yet
        await c.patch(f"/api/v1/projects/{pid}/episodes/1", json={"script_text": "改过的剧本"})
        assert (await c.get(f"/api/v1/projects/{pid}/episodes")).json()[0]["script_text"] == "改过的剧本"


async def test_restoryboard_blocked_once_rendered(engine, claude):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "x", "source_novel_text": "一个创意"})).json()["id"]
        await c.post(f"/api/v1/projects/{pid}/plan", json={})
        await c.post(f"/api/v1/projects/{pid}/episodes/1/storyboard")
        shot = (await c.get(f"/api/v1/projects/{pid}/shots")).json()[0]
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO jobs (shot_id, project_id, task_type, model_key, request_payload)"
                                    " VALUES (:s, :p, 'post.animatic', 'local_animatic', '{}')"),
                               {"s": shot["id"], "p": pid})
        assert (await c.post(f"/api/v1/projects/{pid}/episodes/1/storyboard")).status_code == 409


async def test_plan_needs_source_and_key(engine, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "空"})).json()["id"]
        assert (await c.post(f"/api/v1/projects/{pid}/plan", json={})).status_code == 422
        await c.patch(f"/api/v1/projects/{pid}", json={"source_novel_text": "一个想法"})
        r = await c.post(f"/api/v1/projects/{pid}/plan", json={})
        assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.text


async def test_stage_tiers_and_project_quality_tiers(engine):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        tiers = (await c.get("/api/v1/stage-tiers")).json()
        assert tiers["script"]["standard"]["model"] == "claude-sonnet-5-5"
        assert tiers["script"]["draft"]["model"] == "doubao-seed-2-1-lite-260915"
        assert "video_final" not in tiers  # video stages have their own L0-L3 tiers, not QUALITY_TIERS

        pid = (await c.post("/api/v1/projects", json={"title": "x", "source_novel_text": "一个创意"})).json()["id"]
        r = await c.put(f"/api/v1/projects/{pid}/quality-tiers", json={"tiers": {"script": "draft"}})
        assert r.status_code == 200 and r.json()["tiers"] == {"script": "draft"}
        project = (await c.get(f"/api/v1/projects/{pid}")).json()
        assert "_quality_tiers" not in project["series_bible_json"]  # bookkeeping keys hidden from the API view

        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(engine) as s:
            row = await s.get(script_api.Project, uuid.UUID(pid))
            assert row.series_bible_json["_quality_tiers"] == {"script": "draft"}
        from src.domains.script.script_engine import bible_text
        assert "_quality_tiers" not in bible_text(row.series_bible_json)

        bad = await c.put(f"/api/v1/projects/{pid}/quality-tiers", json={"tiers": {"not_a_stage": "draft"}})
        assert bad.status_code == 422
