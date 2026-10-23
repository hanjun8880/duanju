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
    {"shot_order": 1, "shot_scale": "WS", "duration": 20, "camera_motion": "slow dolly in",
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


async def test_tier_requests(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s:
        draft = await orch.shot_request(s, shots[0].id, "L1_draft")
        # 路由改写：1080p 只在显式 final 时使用（gateway/routing.py），旧断言默认 L2 即 1080p。
        master = await orch.shot_request(s, shots[0].id, "L2_master", final=True)
        everyday = await orch.shot_request(s, shots[0].id, "L2_master")
    assert (draft.resolution, draft.duration, draft.model_key) == ("480p", 20, "seedance_2_5_draft")
    assert (master.resolution, master.duration, master.model_key) == ("1080p", 20, "seedance_2_5_spicy")
    assert (everyday.resolution, everyday.model_key) == ("720p", "seedance_2_5_spicy")
    # 审查意见改写：参考图在出片时迟绑定（锁定图最多 3 张，未锁定只取 1 张），顺序 人物→场景→道具→模板。
    # 旧断言是导入时快照的全部 ref_urls（人物→道具→场景，共 6 张）。
    assert master.prompt.startswith("[参考图1: 林晚 人物] [参考图2: 天台 场景] [参考图3: 怀表 道具]")
    assert master.return_last_frame is True and len(master.refs.image_urls) == 3


async def test_master_model_routed_by_shot_kind(sessionmaker, user_and_project, monkeypatch):
    _, pid = user_and_project
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s, s.begin():
        second = await s.get(Shot, shots[1].id)
        assert second is not None
        second.shot_kind = "action"
    async with sessionmaker() as s:
        action = await orch.shot_request(s, shots[1].id, "L2_master")
        final = await orch.shot_request(s, shots[1].id, "L2_master", final=True)
    assert (action.model_key, action.resolution) == ("wan3_spicy", "720p")
    assert (final.model_key, final.resolution) == ("seedance_2_5_spicy", "1080p")
    monkeypatch.setenv("MODEL_VIDEO_ACTION", "seedance_2_5_spicy")
    async with sessionmaker() as s:
        overridden = await orch.shot_request(s, shots[1].id, "L2_master")
    assert overridden.model_key == "seedance_2_5_spicy"


async def test_refs_bound_late_from_locked_assets(sessionmaker, user_and_project):
    _, pid = user_and_project
    async with sessionmaker() as s:  # storyboard imported before any look-dev exists
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    assert shots[0].refs["asset_names"] == ["林晚", "怀表", "天台"]
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s, s.begin():
        lw = (await s.execute(select(LookdevAsset).where(LookdevAsset.project_id == pid,
                                                         LookdevAsset.name == "林晚"))).scalar_one()
        lw.locked_ref_urls = [f"https://cdn/lock{i}.png" for i in range(5)]
    async with sessionmaker() as s:
        req = await orch.shot_request(s, shots[0].id, "L2_master")
    assert req.refs.image_urls == ["https://cdn/lock0.png", "https://cdn/lock1.png", "https://cdn/lock2.png",
                                   "https://cdn/r0.png", "https://cdn/w.png"]
    assert req.prompt.startswith("[参考图1-3: 林晚 人物] [参考图4: 天台 场景] [参考图5: 怀表 道具]")


async def test_master_chains_previous_last_frame(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s, s.begin():
        first = await s.get(Shot, shots[0].id)
        assert first is not None
        first.last_frame_url = "https://v/last1.png"
    # 审查意见 P0 改写：旧断言要求任何下一镜都用上一镜尾帧作首帧（并因首帧/参考互斥而丢掉全部参考图）。
    # 现在只有 continuous_from_prev=true 的镜头才接上一镜尾帧，其它镜头无首帧、走参考图模式。
    async with sessionmaker() as s:
        cut = await orch.shot_request(s, shots[1].id, "L2_master", chain=True)
    assert cut.first_frame_url is None and cut.refs.image_urls  # hard cut keeps its character refs
    async with sessionmaker() as s, s.begin():
        second = await s.get(Shot, shots[1].id)
        assert second is not None
        second.continuous_from_prev = True
        second.first_frame_url = "https://stale/ignored.png"
    async with sessionmaker() as s:
        chained = await orch.shot_request(s, shots[1].id, "L2_master", chain=True)
        unchained = await orch.shot_request(s, shots[1].id, "L2_master", chain=False)
    assert chained.first_frame_url == "https://v/last1.png" and not chained.refs.any()
    assert unchained.first_frame_url is None


async def test_first_frame_only_from_approved_keyframe(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed_lookdev(sessionmaker, pid)
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s, s.begin():
        first = await s.get(Shot, shots[0].id)
        assert first is not None
        first.keyframe_url, first.keyframe_status = "https://k/1.png", "ready"
        first.first_frame_url = "https://stale/manual.png"  # stray value must not leak in as a first frame
    async with sessionmaker() as s:
        pending = await orch.shot_request(s, shots[0].id, "L2_master")
    assert pending.first_frame_url is None and pending.refs.image_urls
    async with sessionmaker() as s, s.begin():
        first = await s.get(Shot, shots[0].id)
        assert first is not None
        first.keyframe_status = "approved"
    async with sessionmaker() as s:
        approved = await orch.shot_request(s, shots[0].id, "L2_master")
        draft = await orch.shot_request(s, shots[0].id, "L1_draft")
    assert approved.first_frame_url == "https://k/1.png"
    assert draft.first_frame_url == "https://k/1.png"


async def test_last_frame_only_written_to_continuous_next(sessionmaker, user_and_project):
    _, pid = user_and_project
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s:
        await orch.on_shot_rendered(s, shots[0].id, "L2_master", "https://v/a.png", chain=True)
    async with sessionmaker() as s:
        nxt = await s.get(Shot, shots[1].id)
        assert nxt is not None and nxt.first_frame_url is None
    async with sessionmaker() as s, s.begin():
        nxt = await s.get(Shot, shots[1].id)
        assert nxt is not None
        nxt.continuous_from_prev = True
    async with sessionmaker() as s:
        await orch.on_shot_rendered(s, shots[0].id, "L2_master", "https://v/b.png", chain=True)
    async with sessionmaker() as s:
        nxt = await s.get(Shot, shots[1].id)
        assert nxt is not None and nxt.first_frame_url == "https://v/b.png"


async def test_render_and_callback_propagates_last_frame(engine, sessionmaker, user_and_project, vendor):  # noqa: F811
    uid, pid = user_and_project
    v, _ = vendor
    async with engine.begin() as conn:  # a 20s 1080p master costs 64 credits
        await conn.execute(text("UPDATE users SET credits=1000, daily_limit_credits=1000 WHERE id=:u"), {"u": uid})
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    async with sessionmaker() as s, s.begin():  # 审查意见 P0：只有连续镜头才接尾帧，这里显式标记第 2 镜连续
        second = await s.get(Shot, shots[1].id)
        assert second is not None
        second.continuous_from_prev = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/shots/{shots[0].id}/render",  # final=True: keep the 1080p cost check below
                         json={"tier": "L2_master", "chain": True, "final": True})
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
