"""Look-dev candidates vs. locked references, per-episode looks, kinds, dedup and estimate.
Real Postgres; only the Ark HTTP transport is stubbed (fixture imported from test_ark)."""
import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.domains.lookdev.api import on_lookdev_rendered
from src.domains.lookdev.refs import refs_for_episode
from src.main import app, get_http_client
from src.models import LookdevAsset
from tests.test_ark import FakeArk, ark_ready, local_png


@pytest.fixture
def ark(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("POLL_INTERVAL_S", "0")
    fake = FakeArk()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    app.dependency_overrides[get_http_client] = lambda: http
    yield fake
    app.dependency_overrides.clear()


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def make_asset(sessionmaker, pid, **kw) -> LookdevAsset:
    async with sessionmaker() as s, s.begin():
        a = LookdevAsset(project_id=pid, asset_type=kw.pop("asset_type", "character"), name=kw.pop("name", "林晚"),
                         visual_prompt=kw.pop("visual_prompt", "二十七岁，短发，墨绿风衣"), **kw)
        s.add(a)
    return a


async def load(sessionmaker, aid) -> LookdevAsset:
    async with sessionmaker() as s:
        a = await s.get(LookdevAsset, aid)
        assert a is not None
        return a


# --- P0: renders are candidates, never silently become the shot reference ---------------------------------

async def test_render_lands_in_candidates_not_locked(sessionmaker, user_and_project):
    _, pid = user_and_project
    a = await make_asset(sessionmaker, pid, ref_urls=["/media/renders/old.png"], locked_ref_urls=["/media/renders/old.png"])
    async with sessionmaker() as s:
        await on_lookdev_rendered(s, a.id, "/media/renders/new.png")
    b = await load(sessionmaker, a.id)
    assert b.ref_urls == ["/media/renders/new.png", "/media/renders/old.png"]
    assert b.locked_ref_urls == ["/media/renders/old.png"]  # the lock is untouched


async def test_candidates_capped_at_12_and_locked_survive_trim(sessionmaker, user_and_project):
    _, pid = user_and_project
    refs = [f"/media/renders/{i}.png" for i in range(12)]
    a = await make_asset(sessionmaker, pid, ref_urls=refs, locked_ref_urls=[refs[-1]])  # oldest is locked
    async with sessionmaker() as s:
        await on_lookdev_rendered(s, a.id, "/media/renders/new.png")
    b = await load(sessionmaker, a.id)
    assert len(b.ref_urls) == 12 and b.ref_urls[0] == "/media/renders/new.png"
    assert refs[-1] in b.ref_urls and refs[-2] not in b.ref_urls  # dropped the oldest unlocked one


async def test_lock_and_unlock(sessionmaker, user_and_project):
    _, pid = user_and_project
    refs = [f"/media/renders/{i}.png" for i in range(5)]
    a = await make_asset(sessionmaker, pid, ref_urls=refs)
    async with client() as c:
        r = await c.post(f"/api/v1/lookdev/{a.id}/lock", json={"urls": refs[:4]})
        assert r.status_code == 422  # at most 3
        r = await c.post(f"/api/v1/lookdev/{a.id}/lock", json={"urls": ["/media/renders/elsewhere.png"]})
        assert r.status_code == 422  # must be one of this asset's candidates
        r = await c.post(f"/api/v1/lookdev/{a.id}/lock", json={"urls": [refs[2], refs[0]]})
        assert r.status_code == 200, r.text
        assert r.json()["locked_ref_urls"] == [refs[2], refs[0]]
        r = await c.post(f"/api/v1/lookdev/{a.id}/unlock", json={"urls": [refs[2]]})
        assert r.json()["locked_ref_urls"] == [refs[0]]
        r = await c.post(f"/api/v1/lookdev/{a.id}/unlock")
        assert r.json()["locked_ref_urls"] == []
        assert (await c.post(f"/api/v1/lookdev/{uuid.uuid4()}/lock", json={"urls": []})).status_code == 404


async def test_patch_removing_a_candidate_drops_its_lock(sessionmaker, user_and_project):
    _, pid = user_and_project
    a = await make_asset(sessionmaker, pid, ref_urls=["/media/renders/a.png", "/media/renders/b.png"],
                         locked_ref_urls=["/media/renders/a.png", "/media/renders/b.png"])
    async with client() as c:
        r = await c.patch(f"/api/v1/lookdev/{a.id}", json={"ref_urls": ["/media/renders/b.png"]})
    assert r.status_code == 200 and r.json()["locked_ref_urls"] == ["/media/renders/b.png"]


# --- per-episode references ---------------------------------------------------------------------------------

async def test_refs_for_episode_prefers_episode_look_then_lock_then_first(sessionmaker, user_and_project):
    _, pid = user_and_project
    looks = [{"name": "婚纱", "episodes": [5, 7], "locked": ["/media/renders/w.png"]},
             {"name": "空造型", "episodes": [1, 20], "locked": []}]
    a = await make_asset(sessionmaker, pid, ref_urls=["/media/renders/c.png", "/media/renders/w.png"],
                         locked_ref_urls=["/media/renders/l1.png", "/media/renders/l2.png"], meta={"looks": looks})
    async with sessionmaker() as s:
        assert await refs_for_episode(s, a, 6) == ["/media/renders/w.png"]
        assert await refs_for_episode(s, a, 8) == ["/media/renders/l1.png", "/media/renders/l2.png"]
    a2 = await make_asset(sessionmaker, pid, ref_urls=["/media/renders/c.png", "/media/renders/d.png"])
    a3 = await make_asset(sessionmaker, pid, ref_urls=[])
    async with sessionmaker() as s:
        assert await refs_for_episode(s, a2, 1) == ["/media/renders/c.png"]
        assert await refs_for_episode(s, a3, 1) == []
    async with client() as c:
        r = await c.get(f"/api/v1/lookdev/{a.id}/refs_for_episode", params={"episode": 6, "n": 3})
        assert r.status_code == 200 and r.json()["urls"] == ["/media/renders/w.png"]
        assert (await c.get(f"/api/v1/lookdev/{a.id}/refs_for_episode", params={"n": 5})).status_code == 422
        r = await c.get(f"/api/v1/lookdev/{a.id}/refs_for_episode", params={"episode": 8, "n": 1})
        assert r.json()["urls"] == ["/media/renders/l1.png"]


async def test_save_look_validates_against_candidates(sessionmaker, user_and_project):
    _, pid = user_and_project
    a = await make_asset(sessionmaker, pid, ref_urls=["/media/renders/w.png"])
    async with client() as c:
        bad = {"name": "婚纱", "episodes": [5, 7], "locked": ["/media/renders/x.png"]}
        assert (await c.put(f"/api/v1/lookdev/{a.id}/looks/婚纱", json=bad)).status_code == 422
        bad2 = {"name": "婚纱", "episodes": [7, 5], "locked": []}
        assert (await c.put(f"/api/v1/lookdev/{a.id}/looks/婚纱", json=bad2)).status_code == 422
        ok = {"name": "婚纱", "episodes": [5, 7], "locked": ["/media/renders/w.png"]}
        r = await c.put(f"/api/v1/lookdev/{a.id}/looks/婚纱", json=ok)
        assert r.status_code == 200 and r.json()["meta"]["looks"] == [ok]
        r = await c.delete(f"/api/v1/lookdev/{a.id}/looks/婚纱")
        assert r.json()["meta"]["looks"] == []


# --- kinds, references, dedup, estimate ---------------------------------------------------------------------

async def test_generate_kinds_use_locked_refs_and_stage_model(engine, sessionmaker, user_and_project, ark):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake = ark
    a = await make_asset(sessionmaker, pid, ref_urls=["/media/renders/a.png", "/media/renders/b.png"])
    async with client() as c:
        r = await c.post(f"/api/v1/lookdev/{a.id}/generate", json={"kind": "turnaround"})
        assert r.status_code == 201, r.text
        bodies = fake.bodies("/images/generations")
        assert len(bodies) == 1 and "三视图" in bodies[0]["prompt"] and "image" not in bodies[0]  # nothing locked
        assert bodies[0]["model"] == "doubao-seedream-5-0-pro-260628"
        assert (await c.post(f"/api/v1/lookdev/{a.id}/generate", json={"kind": "nope"})).status_code == 422


async def test_generate_with_lock_sends_locked_refs(engine, sessionmaker, user_and_project, ark, monkeypatch):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake = ark
    l1, l2, other = local_png("a.png"), local_png("b.png"), local_png("c.png")
    a = await make_asset(sessionmaker, pid, ref_urls=[other, l1, l2], locked_ref_urls=[l1, l2],
                         meta={"likeness_consent": True})
    async with client() as c:
        r = await c.post(f"/api/v1/lookdev/{a.id}/generate", json={"kind": "expressions"})
        assert r.status_code == 201, r.text
    body = fake.bodies("/images/generations")[-1]
    assert "四宫格" in body["prompt"] and len(body["image"]) == 2


async def test_generate_dedup_hits_cache_and_estimate(engine, sessionmaker, user_and_project, ark, monkeypatch):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake = ark
    a = await make_asset(sessionmaker, pid)
    async with client() as c:
        est = (await c.get(f"/api/v1/lookdev/{a.id}/estimate", params={"kind": "portrait"})).json()
        assert est == {"kind": "portrait", "images": 1, "usd": 0.03, "cached": False}
        r1 = await c.post(f"/api/v1/lookdev/{a.id}/generate", json={"kind": "portrait"})
        r2 = await c.post(f"/api/v1/lookdev/{a.id}/generate")  # default kind = portrait, same prompt+refs
        assert r1.status_code == 201 and r2.status_code == 200, r2.text
        assert r2.json()["cached"] is True and r2.json()["id"] == r1.json()["id"]
        assert len(fake.bodies("/images/generations")) == 1  # nothing re-submitted
        est = (await c.get(f"/api/v1/lookdev/{a.id}/estimate", params={"kind": "portrait"})).json()
        assert est["usd"] == 0 and est["cached"] is True
        r3 = await c.post(f"/api/v1/lookdev/{a.id}/generate", json={"kind": "portrait", "force": True})
        assert r3.status_code == 201 and len(fake.bodies("/images/generations")) == 2
        monkeypatch.setenv("LOOKDEV_USD_PER_IMAGE", "0.05")
        est = (await c.get(f"/api/v1/lookdev/{a.id}/estimate", params={"kind": "turnaround"})).json()
        assert est["usd"] == 0.05
        r = await c.get(f"/api/v1/lookdev/{a.id}/estimate", params={"kind": "costume"})
        assert r.status_code == 422  # costume needs a note


async def test_failed_job_is_not_a_cache_hit(engine, sessionmaker, user_and_project, ark):
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake = ark
    a = await make_asset(sessionmaker, pid)
    async with client() as c:
        r1 = await c.post(f"/api/v1/lookdev/{a.id}/generate")
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE jobs SET status='failed' WHERE id=:j"), {"j": uuid.UUID(r1.json()["id"])})
        r2 = await c.post(f"/api/v1/lookdev/{a.id}/generate")
    assert r2.status_code == 201 and r2.json()["id"] != r1.json()["id"]
    assert len(fake.bodies("/images/generations")) == 2
