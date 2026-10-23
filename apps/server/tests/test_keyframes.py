"""Keyframe stage: refs from locked look-dev, Ark image through the gateway, review, cache, episode batch."""
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src import media
from src.domains.gateway import routing
from src.domains.storyboard import keyframes as kf
from src.main import app
from src.models import LookdevAsset, Shot
from tests.test_ark import ark, ark_ready  # noqa: F401  (fixture)

L = [f"https://cdn.test/lock{i}.png" for i in range(4)]


def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def setup_shots(sessionmaker, pid, n=1):
    async with sessionmaker() as s, s.begin():
        a = LookdevAsset(project_id=pid, asset_type="character", name="林晚", visual_prompt="v",
                         ref_urls=["https://cdn.test/cand.png"], locked_ref_urls=L)
        b = LookdevAsset(project_id=pid, asset_type="scene", name="天台", visual_prompt="v",
                         ref_urls=["https://cdn.test/roof1.png", "https://cdn.test/roof2.png"])
        s.add_all([a, b])
        await s.flush()
        bound = [{"id": str(a.id), "name": a.name, "type": "character"}, {"id": str(b.id), "name": b.name, "type": "scene"}]
        shots = [Shot(project_id=pid, episode_number=1, shot_order=i + 1, shot_prompt=f"林晚在天台转身 {i}",
                      refs={"images": [], "videos": [], "audios": [], "assets": bound}) for i in range(n)]
        s.add_all(shots)
    return shots


async def test_keyframe_request_uses_locked_refs_then_first_candidate(sessionmaker, user_and_project):
    _, pid = user_and_project
    (shot,) = await setup_shots(sessionmaker, pid)
    async with sessionmaker() as s:
        r = await kf.keyframe_request(s, shot)
    assert r.model_key == "seedream_5_pro" and r.task_type == "image.t2i" and r.shot_id == shot.id
    assert r.refs.image_urls == [*L[:3], "https://cdn.test/roof1.png"]
    assert r.prompt.startswith("[参考图1-3: 林晚 人物] [参考图4: 天台 场景]") and "关键帧" in r.prompt
    routing.set_override("keyframe_image", model="flux_uncensored")
    try:
        async with sessionmaker() as s:
            assert (await kf.keyframe_request(s, shot)).model_key == "flux_uncensored"
    finally:
        routing.set_override("keyframe_image")


async def test_generate_review_and_cache(engine, sessionmaker, user_and_project, ark):  # noqa: F811
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    fake, _ = ark
    (shot,) = await setup_shots(sessionmaker, pid)
    async with client() as c:
        assert (await c.post(f"/api/v1/shots/{shot.id}/keyframe/approve")).status_code == 409
        r = await c.post(f"/api/v1/shots/{shot.id}/keyframe")
        assert r.status_code == 201, r.text
        assert r.json()["stage"] == "keyframe_image" and r.json()["usd"] == "0.0300"
        body = fake.bodies("/images/generations")[0]
        assert body["image"] == [*L[:3], "https://cdn.test/roof1.png"] and "林晚" in body["prompt"]
        grid = (await c.get(f"/api/v1/projects/{pid}/episodes/1/keyframes")).json()
        assert grid[0]["keyframe_status"] == "ready" and grid[0]["keyframe_url"].startswith("/media/renders/")
        assert media.local_path(grid[0]["keyframe_url"]) is not None
        r = await c.post(f"/api/v1/shots/{shot.id}/keyframe/reject")
        assert r.json()["keyframe_status"] == "rejected"
        # same shot, same refs, same prompt -> cached: no new vendor call, free, shot is back to ready
        r = await c.post(f"/api/v1/shots/{shot.id}/keyframe")
        assert r.json()["cached_from"] and r.json()["status"] == "success"
        assert len(fake.bodies("/images/generations")) == 1
        assert (await c.get(f"/api/v1/projects/{pid}/episodes/1/keyframes")).json()[0]["keyframe_status"] == "ready"
        r = await c.post(f"/api/v1/shots/{shot.id}/keyframe", params={"force": "true"})
        assert r.json()["cached_from"] is None and len(fake.bodies("/images/generations")) == 2
        r = await c.post(f"/api/v1/shots/{shot.id}/keyframe/approve")
        assert r.json()["keyframe_status"] == "approved"
    async with engine.connect() as conn:
        st = (await conn.execute(text("SELECT keyframe_status FROM shots WHERE id=:s"), {"s": shot.id})).scalar_one()
    assert st == "approved"


async def test_missing_key_resets_pending_and_budget_blocks(engine, sessionmaker, user_and_project, monkeypatch):
    _, pid = user_and_project
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    (shot,) = await setup_shots(sessionmaker, pid)
    async with client() as c:
        r = await c.post(f"/api/v1/shots/{shot.id}/keyframe")
        assert r.status_code == 201
        grid = (await c.get(f"/api/v1/projects/{pid}/episodes/1/keyframes")).json()
        assert grid[0]["keyframe_status"] == "none" and grid[0]["keyframe_url"] is None
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE projects SET budget_usd=0.01 WHERE id=:p"), {"p": pid})
        r = await c.post(f"/api/v1/shots/{shot.id}/keyframe")
        assert r.status_code == 402 and "预算不足" in r.json()["detail"]
        grid = (await c.get(f"/api/v1/projects/{pid}/episodes/1/keyframes")).json()
    assert grid[0]["keyframe_status"] == "none"


async def test_episode_keyframes_only_missing(engine, sessionmaker, user_and_project, ark):  # noqa: F811
    uid, pid = user_and_project
    await ark_ready(engine, uid)
    shots = await setup_shots(sessionmaker, pid, n=3)
    async with client() as c:
        r = await c.post(f"/api/v1/projects/{pid}/episodes/1/keyframes")
        assert r.status_code == 201 and len(r.json()["jobs"]) == 3
        await c.post(f"/api/v1/shots/{shots[1].id}/keyframe/reject")
        r = await c.post(f"/api/v1/projects/{pid}/episodes/1/keyframes", json={"only_missing": True})
        assert [j["shot_id"] for j in r.json()["jobs"]] == [str(shots[1].id)]
        assert (await c.post(f"/api/v1/projects/{pid}/episodes/7/keyframes")).status_code == 404
