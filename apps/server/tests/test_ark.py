"""Volcengine Ark adapter against the documented request/response shapes, real Postgres + ledger.
Only the HTTP transport is stubbed: a fake Ark that queues, runs, then succeeds, and serves the output files."""
import base64
import json
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from src import media
from src.domains.gateway import router
from src.domains.gateway.adapters.ark import ArkAdapter
from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs, preflight
from src.main import app, get_http_client
from src.models import Asset, Job, LookdevAsset
from tests.conftest import balance

ARK = "https://ark.cn-beijing.volces.com/api/v3"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeArk:
    def __init__(self, final: str = "succeeded"):
        self.calls: list[httpx.Request] = []
        self.polls = 0
        self.final = final

    def __call__(self, r: httpx.Request) -> httpx.Response:
        self.calls.append(r)
        path = r.url.path
        if r.url.host == "out.ark.test":
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42" if path.endswith(".mp4") else PNG)
        assert r.headers["authorization"] == "Bearer ark-key"
        if path.endswith("/contents/generations/tasks") and r.method == "POST":
            return httpx.Response(200, json={"id": f"cgt-{uuid.uuid4().hex}"})
        if "/contents/generations/tasks/" in path:
            self.polls += 1
            if self.polls < 3:
                return httpx.Response(200, json={"id": path.rsplit("/", 1)[1], "status": ["queued", "running"][self.polls - 1]})
            if self.final != "succeeded":
                return httpx.Response(200, json={"id": "x", "status": self.final,
                                                 "error": {"code": "OutputVideoSensitive", "message": "blocked"}})
            return httpx.Response(200, json={"id": "x", "status": "succeeded", "duration": 6,
                                             "content": {"video_url": "https://out.ark.test/v.mp4?sig=1",
                                                         "last_frame_url": "https://out.ark.test/last.jpeg"}})
        if path.endswith("/images/generations"):
            return httpx.Response(200, json={"created": 1, "data": [{"url": "https://out.ark.test/img.jpeg",
                                                                     "size": "2048x2048"}]})
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    def bodies(self, suffix: str) -> list[dict]:
        return [json.loads(c.content) for c in self.calls if c.url.path.endswith(suffix) and c.method == "POST"]


@pytest.fixture
def ark(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("POLL_INTERVAL_S", "0")
    fake = FakeArk()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    app.dependency_overrides[get_http_client] = lambda: client
    yield fake, client
    app.dependency_overrides.clear()


def local_png(name: str = "ref.png") -> str:
    p = media.root() / "uploads" / f"{uuid.uuid4().hex}_{name}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(PNG)
    return media.url_for(p)


def test_video_body_first_frame_mode_is_adaptive_and_inlines_local_images():
    url = local_png()
    req = JobCreateRequest(project_id=uuid.uuid4(), task_type="video.i2v", model_key="seedance_2_5_spicy",
                           prompt="转身", duration=8, first_frame_url=url, aspect_ratio="9:16")
    task, canonical = preflight(req)
    body = ArkAdapter().video_body("seedance_2_5_spicy", task, canonical)
    assert body["model"] == "doubao-seedance-2-5-260628"
    assert body["ratio"] == "adaptive"  # Seedance 2.5 first-frame tasks only accept adaptive
    assert body["content"][0] == {"type": "text", "text": "转身"}
    img = body["content"][1]
    assert img["role"] == "first_frame" and img["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["duration"] == 8 and body["watermark"] is False and "omni_reference_task_type" not in body


def test_video_body_reference_mode_roles_and_draft(monkeypatch):
    req = JobCreateRequest(project_id=uuid.uuid4(), task_type="video.i2v", model_key="seedance_2_5_draft",
                           prompt="p", resolution="1080p",
                           refs=MultimodalRefs(image_urls=["https://cdn/a.png", "https://cdn/b.png"],
                                               video_urls=["https://cdn/v.mp4"]))
    task, canonical = preflight(req)
    monkeypatch.setenv("ARK_MODEL_SEEDANCE_2_5_DRAFT", "ep-2026-mine")
    body = ArkAdapter().video_body("seedance_2_5_draft", task, canonical)
    assert body["model"] == "ep-2026-mine"
    assert [c.get("role") for c in body["content"]] == [None, "reference_image", "reference_image", "reference_video"]
    assert body["content"][1]["image_url"]["url"] == "https://cdn/a.png"  # remote URLs pass through
    assert body["omni_reference_task_type"] == "reference" and body["ratio"] == "9:16"
    assert body["draft"] is True and body["resolution"] == "480p"


async def ark_ready(engine, uid):
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET credits=1000, daily_limit_credits=1000 WHERE id=:u"), {"u": uid})


async def test_video_task_polls_downloads_and_settles(engine, sessionmaker, user_and_project, ark):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake, client = ark
    req = JobCreateRequest(project_id=pid, task_type="video.t2v", model_key="seedance_2_5_spicy", prompt="晨光",
                           duration=10, resolution="720p")
    async with sessionmaker() as s:
        job = await router.create_job(s, user_id=uid, req=req)
    estimate = job.credit_estimate
    done = await router.dispatch_job(sessionmaker, job.id, client)
    assert done.status == "success" and done.provider_job_id.startswith("cgt-")
    assert fake.polls == 3  # queued -> running -> succeeded
    sent = fake.bodies("/contents/generations/tasks")[0]
    assert sent["return_last_frame"] is True and "callback_url" not in json.dumps(sent)  # unsigned: we poll
    assert done.actual_credits == estimate * Decimal(6) / Decimal(10)  # billed on Ark's reported 6s
    assert await balance(engine, uid) == Decimal(1000) - done.actual_credits
    async with sessionmaker() as s:
        assets = (await s.execute(select(Asset).where(Asset.job_id == job.id))).scalars().all()
    kinds = {a.kind: a for a in assets}
    assert set(kinds) == {"video", "last_frame"}
    assert kinds["video"].s3_key.endswith(".mp4") and media.local_path(kinds["video"].s3_key) is not None
    assert kinds["last_frame"].meta["source_url"] == "https://out.ark.test/last.jpeg"


async def test_failed_task_refunds_in_full(engine, sessionmaker, user_and_project, ark):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake, client = ark
    fake.final = "failed"
    req = JobCreateRequest(project_id=pid, task_type="video.t2v", model_key="seedance_2_5_spicy", prompt="p")
    async with sessionmaker() as s:
        job = await router.create_job(s, user_id=uid, req=req)
    await router.dispatch_job(sessionmaker, job.id, client)
    async with sessionmaker() as s:
        j = await s.get(Job, job.id)
        assert j is not None and j.status == "failed" and "OutputVideoSensitive" in (j.error_message or "")
    assert await balance(engine, uid) == Decimal(1000)  # wan fallback is unconfigured -> cancelled, refunded


async def test_missing_key_cancels_and_refunds(engine, sessionmaker, user_and_project, monkeypatch):
    uid, pid = user_and_project
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    req = JobCreateRequest(project_id=pid, task_type="video.t2v", model_key="seedance_2_5_spicy", prompt="p")
    async with sessionmaker() as s:
        job = await router.create_job(s, user_id=uid, req=req)
    with pytest.raises(router.ProviderNotConfigured, match="ARK_API_KEY"):
        await router.dispatch_job(sessionmaker, job.id, httpx.AsyncClient())
    assert await balance(engine, uid) == Decimal(100)


async def test_lookdev_generate_saves_image_into_refs(engine, sessionmaker, user_and_project, ark):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake, _ = ark
    async with sessionmaker() as s, s.begin():
        a = LookdevAsset(project_id=pid, asset_type="character", name="林晚", visual_prompt="二十七岁，短发，墨绿风衣")
        s.add(a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/lookdev/{a.id}/generate")
        assert r.status_code == 201, r.text
        assets = (await c.get(f"/api/v1/projects/{pid}/lookdev")).json()
    body = fake.bodies("/images/generations")[0]
    assert body["model"] == "doubao-seedream-5-0-pro-260628" and "林晚" in body["prompt"] and "image" not in body
    refs = next(x for x in assets if x["name"] == "林晚")["ref_urls"]
    assert len(refs) == 1 and refs[0].startswith("/media/renders/") and Path(str(media.local_path(refs[0]))).read_bytes() == PNG


async def test_resume_polls_finishes_processing_jobs(engine, sessionmaker, user_and_project, ark):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    _, client = ark
    req = JobCreateRequest(project_id=pid, task_type="video.t2v", model_key="seedance_2_5_spicy", prompt="p")
    async with sessionmaker() as s:
        job = await router.create_job(s, user_id=uid, req=req)
    async with engine.begin() as conn:  # as if the server died right after Ark accepted the task
        await conn.execute(text("UPDATE jobs SET status='processing', provider_job_id='cgt-resume' WHERE id=:j"),
                           {"j": job.id})
    await router.resume_polls(sessionmaker, client)
    async with sessionmaker() as s:
        j = await s.get(Job, job.id)
        assert j is not None and j.status == "success"


def test_local_path_rejects_traversal():
    assert media.local_path("/media/../../etc/passwd") is None
    assert media.local_path("https://cdn/x.png") is None
