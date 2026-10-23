"""Gateway routing, failover, webhooks and POST /api/v1/jobs against real Postgres.
Only the vendor HTTP transport is stubbed (httpx.MockTransport)."""
import hashlib
import hmac
import json
import uuid
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway import router
from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs
from src.main import app, get_http_client
from tests.conftest import balance

SECRET = "whsec_test"


class Vendor:
    """Scriptable fake vendor endpoint keyed by host."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.behaviour: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        b = self.behaviour.get(request.url.host, 200)
        if isinstance(b, Exception):
            raise b
        if b == 200:
            return httpx.Response(200, json={"task_id": f"{request.url.host}-{uuid.uuid4().hex}"})
        return httpx.Response(int(b), json={"error": "boom"})  # type: ignore[call-overload]

    def bodies(self, host):
        return [json.loads(r.content) for r in self.calls if r.url.host == host]


@pytest.fixture
async def vendor(engine, monkeypatch):
    for p in ("SEEDANCE", "WAN"):
        monkeypatch.setenv(f"{p}_API_KEY", f"key-{p.lower()}")
        monkeypatch.setenv(f"{p}_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://workbench.example")
    async with engine.begin() as conn:
        # these tests exercise the generic signed-webhook HttpAdapter path, so pin the reseller-style provider
        await conn.execute(text("UPDATE model_catalog SET api_base='http://seedance.test', provider='seedance' "
                                "WHERE model_key='seedance_2_5_spicy'"))
        await conn.execute(text("UPDATE model_catalog SET api_base='http://wan.test' WHERE model_key='wan3_spicy'"))
    v = Vendor()
    client = httpx.AsyncClient(transport=httpx.MockTransport(v))
    app.dependency_overrides[get_http_client] = lambda: client
    yield v, client
    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE model_catalog SET api_base='https://ark.cn-beijing.volces.com/api/v3', "
                                "provider='ark' WHERE model_key='seedance_2_5_spicy'"))
        await conn.execute(text("UPDATE model_catalog SET api_base='unconfigured://wan' WHERE model_key='wan3_spicy'"))


def make_req(pid, **kw):
    return JobCreateRequest(project_id=pid, task_type="video.i2v", model_key=kw.pop("model_key", "seedance_2_5_spicy"),
                            prompt="女主在天台晨曦中转身", duration=kw.pop("duration", 10), resolution="720p",
                            refs=MultimodalRefs(image_urls=["https://cdn/x/1.png", "https://cdn/x/2.png"]), **kw)


async def queued_job(sessionmaker, uid, req):
    async with sessionmaker() as s:
        return await router.create_job(s, user_id=uid, req=req)


async def reasons(engine, uid):
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT reason FROM credit_ledger WHERE user_id=:u ORDER BY created_at, balance DESC"), {"u": uid})
        ).scalars().all()


async def test_dispatch_submits_vendor_payload(sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    v, client = vendor
    job = await queued_job(sessionmaker, uid, make_req(pid))
    done = await router.dispatch_job(sessionmaker, job.id, client)
    assert done.status == "processing" and done.provider_job_id.startswith("seedance.test-")
    req = v.calls[0]
    assert req.headers["authorization"] == "Bearer key-seedance"
    body = json.loads(req.content)
    assert body["reference_image_urls"] == ["https://cdn/x/1.png", "https://cdn/x/2.png"]
    assert body["callback_url"] == "https://workbench.example/api/v1/webhooks/seedance"
    assert body["duration"] == 10 and body["return_last_frame"] is True and "first_frame_url" not in body


async def test_5xx_refunds_and_fails_over_to_wan(engine, sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    v, client = vendor
    v.behaviour["seedance.test"] = 503
    job = await queued_job(sessionmaker, uid, make_req(pid))
    final = await router.dispatch_job(sessionmaker, job.id, client)
    assert final.model_key == "wan3_spicy" and final.status == "processing"
    assert final.request_payload["failover_of"] == str(job.id)
    async with sessionmaker() as s:
        orig = await s.get(ledger.Job, job.id)
        assert orig is not None and orig.status == "failed"
    assert await reasons(engine, uid) == ["pre_deduct", "provider_5xx_refund", "pre_deduct"]
    assert await balance(engine, uid) == Decimal(100) - final.credit_estimate
    assert len(v.bodies("wan.test")) == 1


async def test_timeout_refunds_with_timeout_reason(engine, sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    v, client = vendor
    v.behaviour["seedance.test"] = httpx.ReadTimeout("slow")
    await router.dispatch_job(sessionmaker, (await queued_job(sessionmaker, uid, make_req(pid))).id, client)
    assert (await reasons(engine, uid))[1] == "timeout_refund"


async def test_both_vendors_down_refunds_everything(engine, sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    v, client = vendor
    v.behaviour.update({"seedance.test": 502, "wan.test": 500})
    job = await queued_job(sessionmaker, uid, make_req(pid))
    with pytest.raises(router.ProviderUnavailable):
        await router.dispatch_job(sessionmaker, job.id, client)
    assert await balance(engine, uid) == Decimal(100)
    assert len(v.calls) == 2  # one hop only, no seedance<->wan ping-pong


async def test_4xx_refunds_without_failover(engine, sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    v, client = vendor
    v.behaviour["seedance.test"] = 400
    with pytest.raises(router.ProviderRejected):
        await router.dispatch_job(sessionmaker, (await queued_job(sessionmaker, uid, make_req(pid))).id, client)
    assert await reasons(engine, uid) == ["pre_deduct", "provider_4xx_refund"]
    assert v.bodies("wan.test") == []


async def test_unconfigured_provider_is_blocked_and_refunded(engine, sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    v, client = vendor
    job = await queued_job(sessionmaker, uid, make_req(pid, model_key="minimax_h3", duration=5))
    with pytest.raises(router.ProviderNotConfigured):
        await router.dispatch_job(sessionmaker, job.id, client)
    assert await balance(engine, uid) == Decimal(100)
    assert v.calls == []


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


async def post_webhook(provider, payload, signature=None):
    raw = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(f"/api/v1/webhooks/{provider}", content=raw,
                            headers={"X-Webhook-Signature": signature or sign(raw)})


async def test_webhook_success_settles_by_actual_seconds(engine, sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    _, client = vendor
    job = await router.dispatch_job(sessionmaker, (await queued_job(sessionmaker, uid, make_req(pid))).id, client)
    cb = {"task_id": job.provider_job_id, "status": "succeeded", "video_url": "https://v/out.mp4",
          "last_frame_url": "https://v/last.png", "duration": 8}
    r = await post_webhook("seedance", cb)
    assert r.status_code == 200 and r.json()["status"] == "success"
    async with sessionmaker() as s:
        model = await s.get(ledger.ModelCatalog, "seedance_2_5_spicy")
        assert model is not None
        expected = ledger.estimate_credits(model, 8, "720p")
    assert await balance(engine, uid) == Decimal(100) - expected
    async with engine.connect() as conn:
        kinds = (await conn.execute(text("SELECT kind FROM assets WHERE job_id=:j ORDER BY kind"),
                                    {"j": job.id})).scalars().all()
    assert kinds == ["last_frame", "video"]
    dup = await post_webhook("seedance", cb)  # vendor retries the callback
    assert dup.status_code == 200 and dup.json()["status"] == "duplicate"
    assert await balance(engine, uid) == Decimal(100) - expected


async def test_webhook_rejects_bad_signature(user_and_project, vendor):
    r = await post_webhook("seedance", {"task_id": "x", "status": "succeeded"}, signature="deadbeef")
    assert r.status_code == 401


async def test_webhook_failure_refunds_and_fails_over(engine, sessionmaker, user_and_project, vendor):
    uid, pid = user_and_project
    v, client = vendor
    job = await router.dispatch_job(sessionmaker, (await queued_job(sessionmaker, uid, make_req(pid))).id, client)
    r = await post_webhook("seedance", {"task_id": job.provider_job_id, "status": "failed", "error": "policy"})
    assert r.status_code == 200 and r.json().get("failover_job_model") == "wan3_spicy", r.json()
    assert await reasons(engine, uid) == ["pre_deduct", "provider_5xx_refund", "pre_deduct"]
    assert len(v.bodies("wan.test")) == 1


async def api_post_job(body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/api/v1/jobs", json=body)


def body_for(pid, **kw):
    return {"project_id": str(pid), "task_type": "video.i2v", "model_key": "seedance_2_5_spicy",
            "prompt": "天台晨曦长镜头", "duration": 10, "refs": {"image_urls": ["https://cdn/1.png"]}, **kw}


async def test_api_create_job_prededucts_and_dispatches(engine, user_and_project, vendor):
    uid, pid = user_and_project
    v, _ = vendor
    r = await api_post_job(body_for(pid))
    assert r.status_code == 201, r.text
    data = r.json()
    assert Decimal(data["credit_estimate"]) > 0
    assert await balance(engine, uid) == Decimal(100) - Decimal(data["credit_estimate"])
    assert len(v.bodies("seedance.test")) == 1  # background dispatch ran


@pytest.mark.parametrize("override,status", [
    ({"prompt": "loli in bedroom"}, 422),
    ({"task_type": "face.swap", "model_key": "seedance_2_5_spicy"}, 422),
    ({"duration": 30, "model_key": "minimax_h3"}, 422),
    ({"model_key": "no_such_model"}, 422),
])
async def test_api_rejections_move_no_money(engine, user_and_project, vendor, override, status):
    uid, pid = user_and_project
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE projects SET content_mode='uncensored_nsfw' WHERE id=:p"), {"p": pid})
    r = await api_post_job(body_for(pid, **override))
    assert r.status_code == status, r.text
    assert await balance(engine, uid) == Decimal(100)


async def test_api_uncensored_requires_age_verification(engine, user_and_project, vendor):
    uid, pid = user_and_project
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE projects SET content_mode='uncensored_nsfw' WHERE id=:p"), {"p": pid})
        await conn.execute(text("UPDATE users SET age_verified_18plus=false WHERE id=:u"), {"u": uid})
    assert (await api_post_job(body_for(pid))).status_code == 403


async def test_api_face_swap_requires_likeness_consent(engine, user_and_project, vendor):
    _, pid = user_and_project
    r = await api_post_job(body_for(pid, task_type="face.swap", model_key="insightface_swap_pro"))
    assert r.status_code == 422 and "likeness" in r.text
