"""Shots -> L0 animatics -> episode master -> whole-series export, and video remix -> source text.
Real FFmpeg, real faster-whisper(tiny), real Postgres; Claude's transport stubbed for keyframe description."""
import asyncio
import json
import subprocess
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src import media, queue
from src.config import settings
from src.domains.gateway import credit_ledger as ledger
from src.domains.script import script_engine
from src.domains.script.script_engine import EpisodeStoryboardOutput
from src.domains.storyboard import post
from src.domains.storyboard import shot_orchestrator as orch
from src.main import app
from src.models import Job, LookdevAsset, Project
from tests.test_script_engine import claude_response, make_claude
from tests.test_video_remix import three_shot_video  # noqa: F401  (fixture reuse)

BOARD = EpisodeStoryboardOutput.model_validate({"episode_title": "一", "hook_summary": "h", "shots": [
    {"shot_order": i, "shot_scale": "MS", "duration": d, "camera_motion": "static", "five_part_visual_prompt": "p",
     "dialogue": t, "character_names": ["林晚"], "prop_names": [], "scene_name": "", "suggested_pose_template": ""}
    for i, d, t in ((1, 4, "你终于来了。"), (2, 5, "我一直都在。"))]})


def still(color: str) -> str:
    p = media.root() / "uploads" / f"{uuid.uuid4().hex}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=360x640", "-frames:v", "1", str(p)],
                   check=True, capture_output=True)
    return media.url_for(p)


async def local_job(sessionmaker, uid, pid, model, task, payload, shot_id=None):
    async with sessionmaker() as s:
        return await ledger.create_job_with_prededuct(s, user_id=uid, project_id=pid, model_key=model, task_type=task,
                                                      request_payload=payload, billable_units=1, resolution="720p",
                                                      shot_id=shot_id)


@pytest.fixture
def small_frames(monkeypatch):
    monkeypatch.setitem(post.SIZES, "9:16", (360, 640))  # keep the encode fast; same code path


async def test_animatics_to_episode_to_series(engine, sessionmaker, user_and_project, small_frames):
    uid, pid = user_and_project
    async with sessionmaker() as s, s.begin():
        s.add(LookdevAsset(project_id=pid, asset_type="character", name="林晚", visual_prompt="v",
                           ref_urls=[still("0x3b2f2a")]))
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    for sh in shots:  # what POST /shots/{id}/render L0 enqueues, run by the local_post worker body
        job = await local_job(sessionmaker, uid, pid, "local_animatic", "post.animatic",
                              {"shot_id": str(sh.id), "render_tier": "L0_animatic"}, sh.id)
        assert await asyncio.to_thread(queue.animatic, str(job.id)) == "success"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        listed = (await c.get(f"/api/v1/projects/{pid}/shots")).json()
        assert all(x["preview_url"] and x["preview_url"].startswith("/media/") for x in listed)
        assert (await c.post(f"/api/v1/projects/{pid}/assemble", json={})).status_code == 422  # no episode yet

    ep_job = await local_job(sessionmaker, uid, pid, "local_assemble", "post.assemble", {"episode_number": 1})
    assert await asyncio.to_thread(queue.assemble, str(ep_job.id)) == "success"
    series_job = await local_job(sessionmaker, uid, pid, "local_assemble", "post.assemble", {"episode_number": None})
    assert await asyncio.to_thread(queue.assemble, str(series_job.id)) == "success"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        outs = (await c.get(f"/api/v1/projects/{pid}/outputs")).json()
    assert [o["kind"] for o in outs] == ["series", "episode"]
    assert outs[1]["meta"]["shots_used"] == 2 and outs[0]["meta"]["episodes"] == [1]
    ep_file = str(media.local_path(outs[1]["url"]))
    assert abs(post.probe_duration(ep_file) - 9.0) < 0.5
    assert post.has_audio(ep_file)  # silent animatics got a track so vendor clips can join later
    info = json.loads((await asyncio.to_thread(subprocess.run, ["ffprobe", "-v", "error", "-show_streams", "-of",
                                                                "json", ep_file], capture_output=True, text=True,
                                               check=True)).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (360, 640)
    async with sessionmaker() as s:
        statuses = (await s.execute(select(Job.status).where(Job.project_id == pid))).scalars().all()
    assert set(statuses) == {"success"}


async def test_remix_video_becomes_source_text(engine, sessionmaker, user_and_project, three_shot_video,  # noqa: F811
                                               monkeypatch):
    _, pid = user_and_project
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        n = sum(1 for b in body["messages"][0]["content"] if b["type"] == "image")
        return claude_response(json.dumps({"shots": [{"shot_order": i, "description": f"纯色画面{i}"}
                                                     for i in range(1, n + 1)]}, ensure_ascii=False))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(settings, "whisper_size", "tiny")
    Real = script_engine.CachedDirectorLLM
    monkeypatch.setattr(script_engine, "CachedDirectorLLM", lambda **kw: Real(client=make_claude(handler), **kw))

    n = await asyncio.to_thread(queue.remix, str(pid), str(three_shot_video))
    assert n == 3
    async with sessionmaker() as s:
        p = await s.get(Project, pid)
    assert p is not None and p.status == "source_ready"
    assert "镜头1" in p.source_novel_text and "纯色画面3" in p.source_novel_text
    assert "country" in p.source_novel_text.lower()  # whisper dialogue carried along
    req = seen[0]
    assert req["model"] == "claude-sonnet-5-5"
    assert sum(1 for b in req["messages"][0]["content"] if b["type"] == "image") == 3
