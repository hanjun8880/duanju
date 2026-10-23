import json
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from src import media
from src.domains.script.script_engine import EpisodeStoryboardOutput
from src.domains.storyboard import shot_orchestrator as orch
from src.main import app
from src.models import LookdevAsset, Shot
from tests.test_router import SECRET, sign, vendor  # noqa: F401  (fixture reuse)

BOARD = EpisodeStoryboardOutput.model_validate({"episode_title": "第一集", "hook_summary": "怀表停在六点", "shots": [
    {"shot_order": 1, "shot_scale": "WS", "duration": 10, "camera_motion": "slow dolly in",
     "five_part_visual_prompt": "1.天台全景 2.推进 3.林晚转身 4.晨光变暖 5.定格怀表",
     "dialogue": "你终于来了。", "character_names": ["林晚"], "prop_names": ["怀表"], "scene_name": "天台",
     "suggested_pose_template": "window silhouette"},
    {"shot_order": 2, "shot_scale": "CU", "duration": 8, "camera_motion": "static",
     "five_part_visual_prompt": "1.近景 2.固定 3.沈舟抬眼 4.风起 5.定格", "dialogue": "我一直都在。",
     "character_names": ["沈舟"], "prop_names": [], "scene_name": "天台", "suggested_pose_template": "none"}]})


async def seed_lookdev(sessionmaker, pid):
    async with sessionmaker() as s, s.begin():
        s.add_all([
            LookdevAsset(project_id=pid, asset_type="character", name="林晚", visual_prompt="v",
                         ref_urls=[f"https://cdn/lw{i}.png" for i in range(3)]),
            LookdevAsset(project_id=pid, asset_type="character", name="沈舟", visual_prompt="v",
                         ref_urls=[f"https://cdn/sz{i}.png" for i in range(40)]),
            LookdevAsset(project_id=pid, asset_type="prop", name="怀表", visual_prompt="v", ref_urls=["https://cdn/w.png"]),
            LookdevAsset(project_id=pid, asset_type="scene", name="天台", visual_prompt="v",
                         ref_urls=["https://cdn/r0.png", "https://cdn/r1.png"]),
        ])


async def test_import_storyboard_binds_assets_and_templates(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    assert [x.shot_order for x in shots] == [1, 2]
    first = shots[0]
    assert first.template_id is not None  # matched "window silhouette"
    assert first.refs["images"][:3] == [f"https://cdn/lw{i}.png" for i in range(3)]
    assert [a["name"] for a in first.refs["assets"]] == ["林晚", "怀表", "天台"]
    assert first.refs["assets"][0]["range"] == [1, 3] and first.refs["assets"][2]["range"] == [5, 6]
    assert len(shots[1].refs["images"]) == 30  # capped at Seedance's 30-image limit
    assert first.dialogue_transcript == "你终于来了。"


async def test_import_storyboard_writes_kind_continuity_and_speaker(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    board = EpisodeStoryboardOutput.model_validate({"episode_title": "一", "hook_summary": "h", "shots": [
        {"shot_order": i, "shot_scale": "MS", "duration": 6, "camera_motion": "static",
         "five_part_visual_prompt": "p", "dialogue": d, "speaker": sp, "shot_kind": k, "continuous_from_prev": c,
         "character_names": ["林晚"], "prop_names": [], "scene_name": "天台", "suggested_pose_template": ""}
        for i, k, c, d, sp in ((1, "establishing", True, "", ""), (2, "dialogue", False, "你来了。", "林晚"),
                               (3, "action", True, "", "沈舟"), (4, "insert", False, "嗯", "路人"))]})
    async with sessionmaker() as s:
        await orch.import_storyboard(s, pid, 1, board)
    async with sessionmaker() as s:
        rows = (await s.execute(select(Shot).where(Shot.project_id == pid).order_by(Shot.shot_order))).scalars().all()
        ids = {a.name: a.id for a in (await s.execute(select(LookdevAsset).where(
            LookdevAsset.project_id == pid))).scalars()}
    assert [r.shot_kind for r in rows] == ["establishing", "dialogue", "action", "insert"]
    assert [r.continuous_from_prev for r in rows] == [False, False, True, False]  # first shot has nothing before it
    assert [r.speaker_asset_id for r in rows] == [None, ids["林晚"], ids["沈舟"], None]  # unknown name -> None


def test_storyboard_shots_are_short_takes():
    import pytest
    from pydantic import ValidationError

    from src.domains.script import api as script_api
    from src.domains.script.script_engine import ShotSchema, user_prompt
    base = {"shot_order": 1, "shot_scale": "MS", "camera_motion": "s", "five_part_visual_prompt": "p",
            "dialogue": "", "character_names": [], "prop_names": [], "scene_name": "", "suggested_pose_template": ""}
    assert ShotSchema.model_validate({**base, "duration": 10}).shot_kind == "dialogue"
    with pytest.raises(ValidationError):
        ShotSchema.model_validate({**base, "duration": 11})
    assert "15-30" not in script_api.DIRECTOR_RULES and "15-30" not in user_prompt("x")
    assert "4-8" in script_api.DIRECTOR_RULES


async def test_tier_requests(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s:
        draft = await orch.shot_request(s, shots[0].id, "L1_draft")
        master = await orch.shot_request(s, shots[0].id, "L2_master")
    assert (draft.resolution, draft.duration, draft.model_key) == ("480p", 10, "seedance_2_5_draft")
    assert (master.resolution, master.duration, master.model_key) == ("1080p", 10, "seedance_2_5_spicy")
    assert master.prompt.startswith("[参考图1-3: 林晚 人物] [参考图4: 怀表 道具] [参考图5-6: 天台 场景]")
    assert master.return_last_frame is True and len(master.refs.image_urls) == 6


async def test_master_chains_previous_last_frame(sessionmaker, user_and_project):
    _, pid = user_and_project
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s, s.begin():
        first = await s.get(Shot, shots[0].id)
        assert first is not None
        first.last_frame_url = "https://v/last1.png"
    async with sessionmaker() as s:
        chained = await orch.shot_request(s, shots[1].id, "L2_master", chain=True)
        unchained = await orch.shot_request(s, shots[1].id, "L2_master", chain=False)
    assert chained.first_frame_url == "https://v/last1.png" and not chained.refs.any()
    assert unchained.first_frame_url is None


async def test_render_and_callback_propagates_last_frame(engine, sessionmaker, user_and_project, vendor):  # noqa: F811
    uid, pid = user_and_project
    v, _ = vendor
    async with engine.begin() as conn:  # a 20s 1080p master costs 64 credits
        await conn.execute(text("UPDATE users SET credits=1000, daily_limit_credits=1000 WHERE id=:u"), {"u": uid})
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/shots/{shots[0].id}/render", json={"tier": "L2_master", "chain": True})
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        task_id = json.loads(v.calls[-1].content)  # body sent to vendor
        assert task_id["resolution"] == "1080p"
        async with engine.connect() as conn:
            pjid = (await conn.execute(text("SELECT provider_job_id FROM jobs WHERE id=:j"), {"j": job_id})).scalar()
        raw = json.dumps({"task_id": pjid, "status": "succeeded", "video_url": "https://v/1.mp4",
                          "last_frame_url": "https://v/1_last.png", "duration": 20}).encode()
        r = await c.post("/api/v1/webhooks/seedance", content=raw, headers={"X-Webhook-Signature": sign(raw)})
        assert r.status_code == 200 and r.json()["status"] == "success"
    async with sessionmaker() as s:
        rows = (await s.execute(select(Shot).where(Shot.project_id == pid).order_by(Shot.shot_order))).scalars().all()
    # vendor URLs expire, so the chain uses our downloaded copy of the last frame
    kept = rows[0].last_frame_url
    assert rows[0].status == "rendered_L2_master" and kept.startswith("/media/renders/") and kept.endswith(".png")
    assert media.local_path(kept) is not None
    assert rows[1].first_frame_url == kept
    async with engine.connect() as conn:
        bal = (await conn.execute(text("SELECT credits FROM users WHERE id=:u"), {"u": uid})).scalar()
    assert bal == Decimal(1000) - Decimal(64)  # settled on actual 20s @1080p
    assert SECRET


async def test_l0_animatic_render_is_zero_credit_and_local(engine, sessionmaker, user_and_project, monkeypatch):
    uid, pid = user_and_project
    sent = []
    monkeypatch.setattr(orch, "enqueue_local", lambda name, *a: sent.append((name, a)))
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/shots/{shots[0].id}/render", json={"tier": "L0_animatic"})
    assert r.status_code == 202 and r.json()["queue"] == "local_post"
    assert sent and sent[0][0] == "post.animatic"
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT credits FROM users WHERE id=:u"), {"u": uid})).scalar() == 100
