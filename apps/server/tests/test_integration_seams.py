"""Seams between the six team branches (integration on hark/base): one reference rule for every shot/keyframe,
remix compliance, native-audio switch, render force + content-hash cache in a batch, non-render jobs (llm.*, tts,
keyframe stills) kept out of takes, and LLM/TTS spend in the cost summary."""
import uuid
from decimal import Decimal

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from src import media
from src.config import settings
from src.domains.gateway import router
from src.domains.lookdev.refs import refs_for_episode
from src.domains.remix import api as remix_api
from src.domains.remix import modes
from src.domains.script import usage
from src.domains.storyboard import keyframes as kf
from src.domains.storyboard import shot_orchestrator as orch
from src.domains.voice import providers as P
from src.domains.voice import service as S
from src.main import app
from src.models import Asset, Job, LookdevAsset, Shot
from tests.test_orchestrator import BOARD, BOARD3, add_take, seed_lookdev
from tests.test_remix_modes import PLAN, RHYTHM, SOURCE, llm_with
from tests.test_voice import creds, mp3_bytes, volc_transport  # noqa: F401  (fixtures)


def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def rich(engine, uid):
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET credits=1000, daily_limit_credits=1000 WHERE id=:u"), {"u": uid})


async def redesign(sessionmaker, pid, name="林晚"):
    async with sessionmaker() as s, s.begin():
        a = (await s.execute(select(LookdevAsset).where(LookdevAsset.project_id == pid,
                                                        LookdevAsset.name == name))).scalar_one()
        a.locked_ref_urls = ["https://cdn/orig-face.png"]
        a.meta = {**(a.meta or {}), "redesign_required": True}


# --- seam 3: one reference rule (lookdev.refs) for video and keyframes, remix compliance included ---

async def test_redesign_required_binds_nothing_for_video_or_keyframe(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    await redesign(sessionmaker, pid)
    async with sessionmaker() as s:
        a = (await s.execute(select(LookdevAsset).where(LookdevAsset.project_id == pid,
                                                        LookdevAsset.name == "林晚"))).scalar_one()
        assert await refs_for_episode(s, a, 1) == []
        video = await orch.shot_request(s, shots[0].id, "L2_master")
        shot = await s.get(Shot, shots[0].id)
        assert shot is not None
        still = await kf.keyframe_request(s, shot)
    for r in (video, still):
        assert not any("lw" in u or "orig-face" in u for u in r.refs.image_urls)
        assert "林晚 人物" not in r.prompt.split("\n")[0]
        assert "天台 场景" in r.prompt.split("\n")[0]  # the other assets still bind


async def test_keyframe_refs_bound_late_with_episode_look(sessionmaker, user_and_project):
    """Keyframes use the same late binding as renders: assets locked after import and per-episode looks count."""
    _, pid = user_and_project
    async with sessionmaker() as s:  # storyboard first, look-dev later (the snapshot has no assets)
        shots = await orch.import_storyboard(s, pid, 2, BOARD)
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s, s.begin():
        a = (await s.execute(select(LookdevAsset).where(LookdevAsset.project_id == pid,
                                                        LookdevAsset.name == "林晚"))).scalar_one()
        a.locked_ref_urls = ["https://cdn/lw0.png"]
        a.meta = {"looks": [{"name": "婚纱", "episodes": [2, 3], "locked": ["https://cdn/wedding.png"]}]}
    async with sessionmaker() as s:
        shot = await s.get(Shot, shots[0].id)
        assert shot is not None
        still = await kf.keyframe_request(s, shot)
    assert still.refs.image_urls[0] == "https://cdn/wedding.png"
    assert still.prompt.startswith("[参考图1: 林晚 人物] [参考图2: 天台 场景]")


# --- seam 4: native audio off by default ---

async def test_master_generate_audio_follows_video_native_audio(sessionmaker, user_and_project, monkeypatch):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    assert settings.video_native_audio is False
    async with sessionmaker() as s:
        assert (await orch.shot_request(s, shots[0].id, "L2_master")).generate_audio is False
        assert (await orch.shot_request(s, shots[0].id, "L1_draft")).generate_audio is False
    monkeypatch.setattr(settings, "video_native_audio", True)
    async with sessionmaker() as s:
        assert (await orch.shot_request(s, shots[0].id, "L2_master")).generate_audio is True


# --- seam 5: force + content-hash cache in single renders and batches ---

def stored_video() -> str:
    p = media.root() / "renders" / f"{uuid.uuid4().hex}.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return media.url_for(p)


async def rendered_before(sessionmaker, uid, shot_id):
    """An earlier identical L2 render of this shot that settled with its file still on disk."""
    from src.domains.gateway import credit_ledger as ledger
    async with sessionmaker() as s:
        req = await orch.shot_request(s, shot_id, "L2_master")
    async with sessionmaker() as s:
        job = await router.create_job(s, user_id=uid, req=req, meta={"render_tier": "L2_master", "chain": True})
    key = stored_video()
    async with sessionmaker() as s:
        await ledger.settle_job(s, job.id, job.credit_estimate, extra=[
            Asset(job_id=job.id, project_id=job.project_id, kind="video", s3_key=key, meta={"url": key})])
    return job


async def test_batch_cache_hit_still_advances_to_next_shot(engine, sessionmaker, user_and_project, monkeypatch):
    uid, pid = user_and_project
    await rich(engine, uid)
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD3)  # three hard cuts
    first = await rendered_before(sessionmaker, uid, shots[1].id)
    dispatched: list[Job] = []

    async def fake_dispatch(sm, client, job, provider):
        dispatched.append(job)
    monkeypatch.setattr(router, "queue_dispatch", fake_dispatch)
    assert await orch._queue_shot(sessionmaker, None, shots[1].id, "L2_master", True, False) == shots[1].id
    async with sessionmaker() as s:
        hit = (await s.execute(select(Job).where(Job.shot_id == shots[1].id, Job.id != first.id))).scalar_one()
        second = await s.get(Shot, shots[1].id)
    assert hit.status == "success" and hit.request_payload["cached_from"] == str(first.id)
    assert hit.request_payload["batch"] is True
    assert second is not None and second.status == "rendered_L2_master"  # follow-ups ran without a callback
    # ...and the batch moved on: shot 3 was created and sent to the vendor
    assert [j.shot_id for j in dispatched] == [shots[2].id]
    assert dispatched[0].status == "queued" and "forced" not in dispatched[0].request_payload


async def test_batch_force_skips_cache_and_carries_to_later_shots(engine, sessionmaker, user_and_project,
                                                                  monkeypatch):
    uid, pid = user_and_project
    await rich(engine, uid)
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD3)
    await rendered_before(sessionmaker, uid, shots[1].id)
    dispatched: list[Job] = []

    async def fake_dispatch(sm, client, job, provider):
        dispatched.append(job)
    monkeypatch.setattr(router, "queue_dispatch", fake_dispatch)
    await orch._queue_shot(sessionmaker, None, shots[1].id, "L2_master", True, False, True)
    assert len(dispatched) == 1 and dispatched[0].shot_id == shots[1].id
    forced = dispatched[0]
    assert forced.status == "queued" and forced.request_payload["forced"] is True
    assert "cached_from" not in forced.request_payload
    # when the forced take succeeds, the next shot of the batch is forced too
    await router._after_success(sessionmaker, None, forced, {"video": "/media/x.mp4"})
    assert [j.shot_id for j in dispatched] == [shots[1].id, shots[2].id]
    assert dispatched[1].request_payload["forced"] is True


async def test_render_api_force_flag(engine, sessionmaker, user_and_project):
    uid, pid = user_and_project
    await rich(engine, uid)
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    first = await rendered_before(sessionmaker, uid, shots[0].id)
    async with client() as c:
        r = await c.post(f"/api/v1/shots/{shots[0].id}/render", json={"tier": "L2_master"})
        assert r.status_code == 201 and r.json()["cached_from"] == str(first.id) and r.json()["usd"] == "0"
        r2 = await c.post(f"/api/v1/shots/{shots[0].id}/render", json={"tier": "L2_master", "force": True})
    assert r2.status_code == 201 and r2.json()["cached_from"] is None
    async with sessionmaker() as s:
        shot = await s.get(Shot, shots[0].id)
    assert shot is not None and shot.status in ("rendered_L2_master", "pending", "failed")


# --- seam 6/7: llm.*, tts and keyframe rows are not takes; their USD reaches the cost summary ---

async def test_non_render_jobs_stay_out_of_takes_and_count_in_costs(sessionmaker, user_and_project, creds):  # noqa: F811
    _, pid = user_and_project
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    take_id, take_file = await add_take(sessionmaker, pid, shots[0].id, "L1_draft")
    await usage.record_llm_calls(pid, [
        {"stage": "plan", "model": "claude-opus-5-5", "provider": "anthropic",
         "usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
        {"stage": "remix_rewrite", "model": "claude-sonnet-5-5", "provider": "anthropic",
         "usage": {"input_tokens": 0, "output_tokens": 100_000}}], sessionmaker)
    await S.record_tts(pid, shots[0].id, "你终于来了。", P.Voice("volc", "BV700_streaming"), "k", sessionmaker)
    f = media.root() / "renders" / f"still-{uuid.uuid4().hex}.mp4"  # a non-render job that even has a video asset
    f.write_bytes(b"mp4")
    async with sessionmaker() as s, s.begin():
        still = Job(shot_id=shots[0].id, project_id=pid, task_type="image.t2i", model_key="seedream_5_pro",
                    request_payload={"keyframe_shot_id": str(shots[0].id), "stage": "keyframe_image"},
                    status="success")
        s.add(still)
        await s.flush()
        s.add(Asset(job_id=still.id, project_id=pid, kind="video", s3_key=media.url_for(f)))
        tts = (await s.execute(select(Job).where(Job.project_id == pid, Job.task_type == "tts"))).scalar_one()
    async with sessionmaker() as s:
        takes = await orch.shot_takes(s, [shots[0].id])
        best = await orch.best_clips(s, pid, 1)
    assert [t["job_id"] for t in takes[shots[0].id]] == [str(take_id)]
    assert best[shots[0].id] == take_file.resolve()
    async with client() as c:
        for bad in (still.id, tts.id):
            r = await c.post(f"/api/v1/shots/{shots[0].id}/select", json={"job_id": str(bad)})
            assert r.status_code == 422
        costs = (await c.get(f"/api/v1/projects/{pid}/costs")).json()
        jobs = (await c.get("/api/v1/admin/jobs", params={"limit": 500})).json()
    stages = {b["stage"]: b for b in costs["by_stage"]}
    assert stages["plan"]["usd"] == "5.0000" and stages["plan"]["phase"] == "script"
    assert stages["remix_rewrite"]["usd"] == "1.5000" and stages["remix_rewrite"]["phase"] == "remix"
    tts_usd = Decimal(str(settings.tts_usd_per_char["volc"])) * len("你终于来了。")
    assert Decimal(stages["tts"]["usd"]) == tts_usd.quantize(Decimal("0.0001")) and stages["tts"]["phase"] == "voice"
    phases = {p["phase"]: Decimal(p["usd"]) for p in costs["by_phase"]}
    assert phases["script"] == Decimal(5) and phases["remix"] == Decimal("1.5")
    llm_view = next(j for j in jobs if j["task_type"] == "llm.plan" and j["project_id"] == str(pid))
    assert llm_view["shot_id"] is None and llm_view["usd"] == "5.0000"


async def test_tts_cache_miss_writes_one_zero_credit_job(sessionmaker, user_and_project, creds, mp3_bytes):  # noqa: F811
    _, pid = user_and_project
    seen: list[httpx.Request] = []
    v = P.Voice("volc", "BV700_streaming")
    async with httpx.AsyncClient(transport=volc_transport(mp3_bytes, seen)) as c:
        a = await S.speak(c, "我一直都在。", v, project_id=pid, sm=sessionmaker)
        b = await S.speak(c, "我一直都在。", v, project_id=pid, sm=sessionmaker)
    assert not a.cached and b.cached and len(seen) == 1
    async with sessionmaker() as s:
        rows = (await s.execute(select(Job).where(Job.project_id == pid, Job.task_type == "tts"))).scalars().all()
    assert len(rows) == 1
    j = rows[0]
    assert j.credit_estimate == 0 and j.status == "success" and j.model_key == "tts"
    assert j.request_payload["provider"] == "volc" and j.request_payload["chars"] == len("我一直都在。")
    assert j.request_payload["usd"] == round(settings.tts_usd_per_char["volc"] * len("我一直都在。"), 6)


async def test_remix_rewrite_is_ledgered_on_its_stage(sessionmaker, user_and_project, monkeypatch):
    _, pid = user_and_project
    seen: list = []
    monkeypatch.setattr(remix_api, "make_llm", lambda model: llm_with(seen, PLAN))
    spec = modes.RemixSpec(modes=["restyle"], style="赛博朋克")
    plan = await remix_api._rewrite(spec, SOURCE, RHYTHM, {}, "standard_sfw", True, bill_to=pid)
    assert plan.title == PLAN["title"] and len(seen) == 1
    async with sessionmaker() as s:
        rows = (await s.execute(select(Job).where(Job.project_id == pid))).scalars().all()
    assert [r.task_type for r in rows] == ["llm.remix_rewrite"] and rows[0].model_key == "llm_remix_rewrite"
