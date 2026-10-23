"""Multi-episode remix through the real API: register -> local deconstruct (real FFmpeg/PySceneDetect/whisper tiny)
-> estimate -> describe (Claude transport stubbed) -> generate a new project -> fork it. Real Postgres."""
import json
import shutil
import subprocess
import uuid

import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src import media
from src.config import settings
from src.domains.gateway.routing import model_for
from src.domains.remix import api as remix_api
from src.domains.remix import tasks
from src.domains.script.script_engine import CachedDirectorLLM
from src.main import app
from src.models import LookdevAsset, Project
from tests.test_remix_modes import PLAN
from tests.test_script_engine import claude_response, make_claude

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


@pytest.fixture
def episode_video() -> str:
    """9s red/green/blue cut, unique bytes per run (metadata) so the sha256 cache starts cold."""
    out = media.root() / "uploads" / f"{uuid.uuid4().hex}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    inputs = [a for c in ("red", "green", "blue") for a in ("-f", "lavfi", "-i", f"color=c={c}:s=320x240:r=25:d=3")]
    subprocess.run(["ffmpeg", "-y", *inputs, "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a", "-shortest",
                    "-metadata", f"comment={uuid.uuid4().hex}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True, capture_output=True)
    return str(out)


@pytest.fixture
def claude(monkeypatch):
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append(body)
        if '"episodes"' in json.dumps(body["output_config"]["format"]["schema"]):
            return claude_response(json.dumps(PLAN, ensure_ascii=False))
        orders = [int(b["text"].split("镜头 ")[1].split("（")[0]) for b in body["messages"][0]["content"]
                  if b["type"] == "text" and b["text"].startswith("镜头 ")]
        return claude_response(json.dumps({"shots": [{"shot_order": o, "description": f"纯色画面{o}"}
                                                     for o in orders]}, ensure_ascii=False))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(settings, "whisper_size", "tiny")
    factory = lambda model: CachedDirectorLLM(client=make_claude(handler), model=model)
    monkeypatch.setattr(tasks, "make_llm", factory)
    monkeypatch.setattr(remix_api, "make_llm", factory)
    return seen


async def test_multi_episode_remix_to_new_project_and_fork(engine, sessionmaker, episode_video, claude):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "原片", "category": "remix"})).json()["id"]
        base = f"/api/v1/remix/projects/{pid}"
        for ep in (1, 2):  # same bytes twice: the second episode must hit the cache
            r = await c.post(f"{base}/sources", json={"episode_number": ep, "video_path": episode_video})
            assert r.status_code == 201, r.text
        assert (await c.post(f"{base}/sources", json={"episode_number": 3, "video_path": "/etc/passwd"})
                ).status_code == 422
        assert (await c.post(f"{base}/generate", json={"spec": {"modes": ["sequel"]}})).status_code == 409

        r = await c.post(f"{base}/deconstruct", json={"describe": False})
        assert r.status_code == 202 and r.json()["queued"] == [1, 2]
        srcs = (await c.get(f"{base}/sources")).json()
        assert [(s["episode_number"], s["status"], s["shots"], s["kept"]) for s in srcs] == \
            [(1, "deconstructed", 3, 3), (2, "deconstructed", 3, 3)]
        assert [s["cache_hit"] for s in srcs] == [False, True]
        assert srcs[0]["sha256"] == srcs[1]["sha256"] and srcs[0]["progress"] == 1.0
        assert claude == []  # deconstruct is zero-token

        est = (await c.get(f"{base}/estimate")).json()
        assert est["model"] == model_for("remix_vision").model
        assert [e["images"] for e in est["episodes"]] == [3, 0]  # ep2 shares ep1's frames: billed once
        assert est["total"]["images"] == 3 and est["total"]["usd"] > 0 and est["price_verified"] is False

        r = await c.post(f"{base}/deconstruct", json={"describe": True})
        assert r.status_code == 202
        srcs = (await c.get(f"{base}/sources")).json()
        assert [s["status"] for s in srcs] == ["described", "described"]
        vision = [b for b in claude if b["model"] == model_for("remix_vision").model]
        assert len(vision) == 1  # ep2 reused ep1's cached notes
        assert sum(1 for b in vision[0]["messages"][0]["content"] if b["type"] == "image") == 3
        assert (await c.get(f"{base}/estimate")).json()["total"]["usd"] == 0  # everything cached now

        r = await c.post(f"{base}/generate", json={"title": "雨夜新篇", "spec": {
            "modes": ["restyle", "replot"], "style": "民国", "plot_direction": "改成复仇线"}})
        assert r.status_code == 201, r.text
        new = r.json()
        assert new["category"] == "remix" and new["status"] == "planned" and new["title"] == "雨夜新篇"
        rewrite = claude[-1]
        assert rewrite["model"] == model_for("remix_rewrite").model
        assert "纯色画面2" in rewrite["messages"][0]["content"] and "民国" in rewrite["messages"][0]["content"]
        bible = new["series_bible_json"]
        assert bible["rhythm"]["avg_shots_per_episode"] == 3.0
        assert bible["remix"] == {"modes": ["restyle", "replot"], "source_project_id": pid, "external": True}

        async with sessionmaker() as s:
            src_p = await s.get(Project, uuid.UUID(pid))
            chars = (await s.execute(select(LookdevAsset).where(
                LookdevAsset.project_id == uuid.UUID(new["id"]), LookdevAsset.asset_type == "character"))).scalars().all()
        assert src_p.series_bible_json["rhythm"]["episodes"][0]["shot_count"] == 3
        assert chars and all(a.meta["redesign_required"] and a.ref_urls == [] for a in chars)

        aid = str(chars[0].id)
        assert (await c.post(f"/api/v1/remix/projects/{new['id']}/assets/{aid}/redesigned")).status_code == 422
        async with sessionmaker() as s, s.begin():
            (await s.get(LookdevAsset, chars[0].id)).locked_ref_urls = ["/media/new.png"]
        r = await c.post(f"/api/v1/remix/projects/{new['id']}/assets/{aid}/redesigned")
        assert r.status_code == 200 and r.json()["redesign_required"] is False

        r = await c.post("/api/v1/remix/fork", json={"source_project_id": new["id"]})
        assert r.status_code == 201, r.text
        forked = r.json()
        assert forked["series_bible_json"]["inherited_from"] == new["id"] and forked["episodes"] == 2
        n_calls = len(claude)
        r = await c.post("/api/v1/remix/fork", json={"source_project_id": new["id"], "title": "续集",
                                                     "spec": {"modes": ["sequel"], "episode_count": 2}})
        assert r.status_code == 201 and r.json()["title"] == "续集"
        assert len(claude) == n_calls + 1 and "续写" in claude[-1]["messages"][0]["content"]
        assert (await c.post("/api/v1/remix/fork", json={"source_project_id": str(uuid.uuid4())})).status_code == 404


async def test_describe_needs_key_and_sfw(engine, episode_video, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "x", "category": "remix"})).json()["id"]
        base = f"/api/v1/remix/projects/{pid}"
        await c.post(f"{base}/sources", json={"episode_number": 1, "video_path": episode_video})
        assert (await c.post(f"{base}/deconstruct", json={"describe": True})).status_code == 503
        assert (await c.get(f"{base}/sources")).json()[0]["status"] == "registered"  # nothing queued
        assert (await c.delete(f"{base}/sources/1")).status_code == 204
        assert (await c.get(f"{base}/sources")).json() == []
        assert (await c.get("/api/v1/remix/projects/00000000-0000-0000-0000-000000000000/sources")).status_code == 404


async def test_broken_video_fails_visibly_and_task_is_registered(engine, monkeypatch):
    from src.queue import celery_app
    assert "remix.episode" in celery_app.tasks
    monkeypatch.setattr(settings, "whisper_size", "tiny")
    bad = media.root() / "uploads" / f"{uuid.uuid4().hex}.mp4"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\x00garbage" * 64)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid = (await c.post("/api/v1/projects", json={"title": "x", "category": "remix"})).json()["id"]
        base = f"/api/v1/remix/projects/{pid}"
        await c.post(f"{base}/sources", json={"episode_number": 1, "video_path": str(bad)})
        assert (await c.post(f"{base}/deconstruct", json={})).status_code == 202
        src = (await c.get(f"{base}/sources")).json()[0]
        assert src["status"] == "failed" and src["error"]
        assert (await c.post(f"{base}/deconstruct", json={"episodes": [9]})).status_code == 422
