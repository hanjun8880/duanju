"""USD pricing, budgets, content-hash cache, stage overrides, estimate and cost views (real Postgres)."""
import uuid
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src import media
from src.config import settings
from src.domains.gateway import credit_ledger as ledger
from src.domains.gateway import router, routing
from src.domains.gateway.schemas import JobCreateRequest
from src.main import app
from src.models import Asset, ModelCatalog
from tests.conftest import balance


def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def req(pid, **kw):
    return JobCreateRequest(project_id=pid, task_type="video.t2v", model_key=kw.pop("model_key", "seedance_2_5_spicy"),
                            prompt=kw.pop("prompt", "晨光里转身"), duration=kw.pop("duration", 10),
                            resolution=kw.pop("resolution", "720p"), **kw)


async def new_job(sm, uid, r, **kw):
    async with sm() as s:
        return await router.create_job(s, user_id=uid, req=r, **kw)


async def rich(engine, uid):
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET credits=1000, daily_limit_credits=1000 WHERE id=:u"), {"u": uid})


async def test_migration_seeds_usd_reference_prices_and_stage_table(engine):
    async with engine.connect() as conn:
        usd = dict((await conn.execute(text("SELECT model_key, usd_per_unit FROM model_catalog"))).all())
        cols = (await conn.execute(text("SELECT column_name FROM information_schema.columns "
                                        "WHERE table_name='stage_overrides'"))).scalars().all()
    assert usd["seedance_2_5_spicy"] == Decimal("0.2310") and usd["seedance_2_5_draft"] == Decimal("0.0920")
    assert usd["wan3_spicy"] == Decimal("0.0830") and usd["minimax_h3"] == Decimal("0.0800")
    assert usd["seedream_5_pro"] == Decimal("0.0417")
    assert {"stage", "model", "provider", "resolution", "note"} <= set(cols)


async def test_estimate_usd_resolution_factor_and_credit_fallback(sessionmaker):
    assert settings.credit_usd == 0.01
    async with sessionmaker() as s:
        sd = await s.get(ModelCatalog, "seedance_2_5_spicy")
        flux = await s.get(ModelCatalog, "flux_uncensored")
        assert sd is not None and flux is not None
        assert ledger.estimate_usd(sd, 10, "720p") == Decimal("2.3100")
        assert ledger.estimate_usd(sd, 10, "1080p") == Decimal("5.6900")  # 0.569/0.231 factor
        assert flux.usd_per_unit is None
        assert ledger.estimate_usd(flux, 1, "720p") == ledger.estimate_credits(flux, 1, "720p") * Decimal("0.01")


async def test_job_carries_usd_and_stage(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    job = await new_job(sessionmaker, uid, req(pid))
    assert job.request_payload["usd_estimate"] == "2.3100" and job.request_payload["stage"] == "video_dialogue"
    assert ledger.job_usd(job) == Decimal("2.3100") and job.content_hash and len(job.content_hash) == 64
    async with sessionmaker() as s:
        done = await ledger.settle_job(s, job.id, job.credit_estimate / 2)
    assert ledger.job_usd(done) == Decimal("1.1550")  # settled share
    async with sessionmaker() as s:
        draft = await new_job(sessionmaker, uid, req(pid, model_key="seedance_2_5_draft", resolution="480p",
                                                     prompt="草稿"))
    assert draft.request_payload["stage"] == "video_draft" and draft.request_payload["usd_estimate"] == "0.9200"


async def test_project_budget_counts_in_flight_and_ignores_refunded(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE projects SET budget_usd=3 WHERE id=:p"), {"p": pid})
    first = await new_job(sessionmaker, uid, req(pid))  # $2.31 in flight
    with pytest.raises(ledger.BudgetExceeded, match="项目预算不足") as e:
        await new_job(sessionmaker, uid, req(pid, prompt="另一镜"))
    assert "已花 $0.00" in str(e.value) and "进行中 $2.31" in str(e.value)
    before = await balance(engine, uid)
    async with client() as c:
        r = await c.post("/api/v1/jobs", json=req(pid, prompt="第三镜").model_dump(mode="json"))
    assert r.status_code == 402 and "预算不足" in r.json()["detail"]
    assert await balance(engine, uid) == before  # rejected before any pre-deduct
    async with sessionmaker() as s:
        await ledger.refund_job(s, first.id, reason="provider_5xx_refund")
    assert (await new_job(sessionmaker, uid, req(pid, prompt="另一镜"))).status == "queued"


async def test_episode_budget_and_daily_cap(engine, sessionmaker, user_and_project, monkeypatch):
    uid, pid = user_and_project
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO episodes (project_id, episode_number, budget_usd) VALUES (:p, 2, 1)"),
                           {"p": pid})
        sid = (await conn.execute(text("INSERT INTO shots (project_id, episode_number, shot_order, shot_prompt) "
                                       "VALUES (:p, 2, 1, 'x') RETURNING id"), {"p": pid})).scalar_one()
    with pytest.raises(ledger.BudgetExceeded, match="第 2 集预算不足"):
        await new_job(sessionmaker, uid, req(pid, shot_id=sid))
    assert (await new_job(sessionmaker, uid, req(pid))).status == "queued"  # no shot -> not in episode 2
    monkeypatch.setattr(settings, "daily_budget_usd", 4.0)
    with pytest.raises(ledger.BudgetExceeded, match="今日预算不足"):
        await new_job(sessionmaker, uid, req(pid, prompt="今日第二镜"))
    # zero-cost local jobs are never blocked
    async with sessionmaker() as s:
        local = await ledger.create_job_with_prededuct(
            s, user_id=uid, project_id=pid, model_key="local_animatic", task_type="post.animatic",
            request_payload={}, billable_units=5, resolution="720p", shot_id=sid)
    assert local.status == "queued" and local.request_payload["usd_estimate"] == "0.0000"


def stored_file(name="v.mp4") -> str:
    p = media.root() / "renders" / f"{uuid.uuid4().hex}_{name}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return media.url_for(p)


async def settle_with(sessionmaker, job, key):
    async with sessionmaker() as s:
        await ledger.settle_job(s, job.id, job.credit_estimate, extra=[
            Asset(job_id=job.id, project_id=job.project_id, kind="video", s3_key=key, meta={"url": key})])


async def test_content_hash_cache_reuses_identical_render_for_free(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    first = await new_job(sessionmaker, uid, req(pid))
    key = stored_file()
    await settle_with(sessionmaker, first, key)
    bal = await balance(engine, uid)
    hit = await new_job(sessionmaker, uid, req(pid))
    assert hit.status == "success" and hit.credit_estimate == 0 and hit.actual_credits == 0
    assert hit.request_payload["cached_from"] == str(first.id) and ledger.job_usd(hit) == 0
    assert await balance(engine, uid) == bal
    async with engine.connect() as conn:
        keys = (await conn.execute(text("SELECT s3_key FROM assets WHERE job_id=:j"), {"j": hit.id})).scalars().all()
    assert keys == [key]
    forced = await new_job(sessionmaker, uid, req(pid), force=True)
    assert forced.status == "queued" and forced.credit_estimate > 0
    other = await new_job(sessionmaker, uid, req(pid, prompt="不同的提示词"))
    assert other.status == "queued"
    # cached job never reaches a vendor
    vendor_calls: list[httpx.Request] = []
    c = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: vendor_calls.append(r) or httpx.Response(500)))
    assert (await router.dispatch_job(sessionmaker, hit.id, c)).status == "success" and not vendor_calls


async def test_cache_skipped_when_output_file_is_gone(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    first = await new_job(sessionmaker, uid, req(pid, prompt="文件丢了"))
    key = stored_file()
    await settle_with(sessionmaker, first, key)
    p = media.local_path(key)
    assert p is not None
    p.unlink()
    assert (await new_job(sessionmaker, uid, req(pid, prompt="文件丢了"))).status == "queued"


async def test_api_force_flag_and_cached_view(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    first = await new_job(sessionmaker, uid, req(pid, prompt="接口缓存"))
    await settle_with(sessionmaker, first, stored_file())
    body = req(pid, prompt="接口缓存").model_dump(mode="json")
    async with client() as c:
        r = await c.post("/api/v1/jobs", json=body)
        assert r.status_code == 201 and r.json()["cached_from"] == str(first.id) and r.json()["usd"] == "0"
        r2 = await c.post("/api/v1/jobs", params={"force": "true"}, json=body)
    assert r2.json()["cached_from"] is None and r2.json()["status"] in ("queued", "cancelled", "failed")


@pytest.fixture
def clean_overrides():
    yield
    for s in routing.STAGES:
        routing.set_override(s)


async def test_admin_stages_persist_and_reload(engine, clean_overrides):
    async with client() as c:
        rows = (await c.get("/api/v1/admin/stages")).json()
        kf = next(r for r in rows if r["stage"] == "keyframe_image")
        assert kf["model"] == "seedream_5_pro" and kf["overridden"] is False
        r = await c.put("/api/v1/admin/stages", json={"stage": "video_action", "model": "minimax_h3",
                                                      "resolution": "720p", "note": "动作改用 MiniMax"})
        assert r.status_code == 200 and r.json()["model"] == "minimax_h3" and r.json()["provider"] == "minimax"
        assert routing.model_for("video_action").model == "minimax_h3"
        assert (await c.put("/api/v1/admin/stages", json={"stage": "video_action", "model": "seedream_5_pro"})
                ).status_code == 422
        assert (await c.put("/api/v1/admin/stages", json={"stage": "nope", "model": "x"})).status_code == 404
    routing.set_override("video_action")  # as after a restart
    from src.db import SessionLocal
    from src.domains.gateway.api import load_stage_overrides
    assert await load_stage_overrides(SessionLocal) >= 1
    assert routing.model_for("video_action").model == "minimax_h3"
    async with client() as c:
        r = await c.put("/api/v1/admin/stages", json={"stage": "video_action", "model": None})
    assert r.json()["model"] == "wan3_spicy" and r.json()["overridden"] is False
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM stage_overrides WHERE stage='video_action'"))
                ).scalar_one() == 0


async def test_episode_estimate_follows_stage_routing(engine, user_and_project, clean_overrides):
    _, pid = user_and_project
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO shots (project_id, episode_number, shot_order, shot_prompt, shot_kind, "
                                "duration) VALUES (:p,1,1,'a','dialogue',5), (:p,1,2,'b','action',5)"), {"p": pid})
    async with client() as c:
        e = (await c.get(f"/api/v1/projects/{pid}/episodes/1/estimate")).json()
        assert [s["model_key"] for s in e["shots"]] == ["seedance_2_5_spicy", "wan3_spicy"]
        assert e["usd"] == "1.5700"  # 0.231*5 + 0.083*5
        assert e["keyframes"] == {"count": 2, "model_key": "seedream_5_pro", "usd": "0.0834"}
        assert e["total_usd"] == "1.6534" and Decimal(e["credits"]) > 0
        fin = (await c.get(f"/api/v1/projects/{pid}/episodes/1/estimate", params={"final": "true"})).json()
        assert {s["resolution"] for s in fin["shots"]} == {"1080p"} and fin["usd"] == "5.6900"
        d = (await c.get(f"/api/v1/projects/{pid}/episodes/1/estimate", params={"tier": "L1_draft"})).json()
        assert d["usd"] == "0.9200"
        assert (await c.get(f"/api/v1/projects/{pid}/episodes/9/estimate")).status_code == 404


async def test_project_costs_by_stage_and_budget_endpoints(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    a = await new_job(sessionmaker, uid, req(pid, prompt="一"))
    await settle_with(sessionmaker, a, stored_file())
    await new_job(sessionmaker, uid, req(pid, model_key="seedance_2_5_draft", resolution="480p", prompt="二"))
    await new_job(sessionmaker, uid, req(pid, prompt="一"))  # cache hit
    async with client() as c:
        r = await c.put(f"/api/v1/projects/{pid}/budget", json={"budget_usd": "20"})
        assert r.json()["budget_usd"] == "20.00"
        costs = (await c.get(f"/api/v1/projects/{pid}/costs")).json()
    by = {b["stage"]: b for b in costs["by_stage"]}
    assert by["video_dialogue"]["usd"] == "2.3100" and by["video_dialogue"]["jobs"] == 2
    assert by["video_dialogue"]["cached"] == 1
    assert by["video_draft"]["in_flight_usd"] == "0.9200"
    assert costs["spent_usd"] == "2.3100" and costs["total_usd"] == "3.2300" and costs["budget_usd"] == "20.00"
