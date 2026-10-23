"""Fork an own project (inherit bible, episodes, locked looks, establishing plates; nothing regenerated) and the
external-source compliance rule (characters from someone else's footage must be redesigned). Real Postgres."""
import pytest
from sqlalchemy import select

from src.domains.remix import compliance, fork
from src.domains.remix.modes import RemixSpec
from src.domains.script.script_engine import SeriesPlan
from src.models import Episode, LookdevAsset, Project, Shot

BIBLE = {"title": "雾港来信", "logline": "l", "characters": [{"name": "林晚"}], "props": [], "scenes": []}


async def seed(sessionmaker, pid):
    async with sessionmaker() as s, s.begin():
        p = await s.get(Project, pid)
        p.series_bible_json, p.source_novel_text, p.status = BIBLE, "第一章 雾港", "scripted"
        s.add_all([Episode(project_id=pid, episode_number=1, title="信", synopsis="林晚收到信", script_text="场1",
                           status="scripted"),
                   Episode(project_id=pid, episode_number=2, title="表", synopsis="怀表停了")])
        s.add_all([LookdevAsset(project_id=pid, asset_type="character", name="林晚", visual_prompt="短发墨绿风衣",
                                ref_urls=["/media/a.png", "/media/b.png"], locked_ref_urls=["/media/a.png"],
                                voice={"provider": "volc_tts", "voice_id": "v1"}, meta={"likeness_consent": True}),
                   LookdevAsset(project_id=pid, asset_type="prop", name="怀表", visual_prompt="黄铜怀表",
                                locked_ref_urls=["/media/w.png"]),
                   LookdevAsset(project_id=pid, asset_type="scene", name="天台", visual_prompt="老楼天台",
                                locked_ref_urls=["/media/roof.png"])])
        s.add_all([Shot(project_id=pid, episode_number=1, shot_order=1, shot_prompt="天台晨雾空镜",
                        shot_kind="establishing", keyframe_url="/media/k1.png", keyframe_status="approved"),
                   Shot(project_id=pid, episode_number=1, shot_order=2, shot_prompt="林晚读信", shot_kind="dialogue")])


async def test_fork_inherits_everything_without_regenerating(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed(sessionmaker, pid)
    async with sessionmaker() as s:
        new = await fork.fork_project(s, pid)
    assert new.id != pid and new.category == "remix" and new.status == "scripted"
    assert new.title == "t（二创）"
    assert new.series_bible_json == {**BIBLE, "inherited_from": str(pid)}
    async with sessionmaker() as s:
        eps = (await s.execute(select(Episode).where(Episode.project_id == new.id)
                               .order_by(Episode.episode_number))).scalars().all()
        assets = {a.name: a for a in (await s.execute(select(LookdevAsset).where(
            LookdevAsset.project_id == new.id))).scalars()}
        shots = (await s.execute(select(Shot).where(Shot.project_id == new.id))).scalars().all()
        old_lin = (await s.execute(select(LookdevAsset).where(LookdevAsset.project_id == pid,
                                                              LookdevAsset.name == "林晚"))).scalar_one()
    assert [(e.episode_number, e.title, e.script_text) for e in eps] == [(1, "信", "场1"), (2, "表", None)]
    lin = assets["林晚"]
    assert lin.locked_ref_urls == ["/media/a.png"] and lin.ref_urls == ["/media/a.png", "/media/b.png"]
    assert lin.voice == {"provider": "volc_tts", "voice_id": "v1"}
    assert lin.meta["inherited_from"] == {"project_id": str(pid), "asset_id": str(old_lin.id)}
    assert assets["天台"].locked_ref_urls == ["/media/roof.png"]
    assert len(shots) == 1 and shots[0].shot_kind == "establishing"  # 空镜 reused, story shots re-boarded later
    assert shots[0].keyframe_url == "/media/k1.png" and shots[0].keyframe_status == "approved"
    assert shots[0].source_remix_meta["inherited_from"]["project_id"] == str(pid)


async def test_fork_missing_project(sessionmaker):
    import uuid
    async with sessionmaker() as s:
        with pytest.raises(LookupError):
            await fork.fork_project(s, uuid.uuid4())


PLAN = SeriesPlan.model_validate({"title": "雾港新篇", "logline": "l2", "characters": [
    {"name": "林晚", "role_tag": "主角", "visual_prompt": "改成男性律师，寸头", "personality": "p"},
    {"name": "周叙", "role_tag": "配角", "visual_prompt": "新角色", "personality": "p"}],
    "props": [{"name": "怀表", "visual_prompt": "LLM 重新描述的怀表"}], "scenes": [],
    "episodes": [{"episode_number": 1, "title": "续一", "synopsis": "s", "source_segments": []}]})


async def test_apply_plan_keeps_locked_looks_except_recast(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed(sessionmaker, pid)
    async with sessionmaker() as s:
        new = await fork.fork_project(s, pid, title="续集")
    spec = RemixSpec(modes=["recast", "sequel"], recast={"林晚": "改成男性律师"})
    async with sessionmaker() as s:
        await fork.apply_plan(s, new.id, PLAN, spec, rhythm={"episodes": []})
    async with sessionmaker() as s:
        p = await s.get(Project, new.id)
        assets = {a.name: a for a in (await s.execute(select(LookdevAsset).where(
            LookdevAsset.project_id == new.id))).scalars()}
        eps = (await s.execute(select(Episode).where(Episode.project_id == new.id))).scalars().all()
    assert p.series_bible_json["title"] == "雾港新篇" and p.series_bible_json["inherited_from"] == str(pid)
    assert p.series_bible_json["remix"] == {"modes": ["recast", "sequel"]} and "rhythm" in p.series_bible_json
    assert p.status == "planned" and [e.title for e in eps] == ["续一"]
    assert assets["林晚"].locked_ref_urls == [] and assets["林晚"].visual_prompt == "改成男性律师，寸头"
    assert assets["林晚"].meta["needs_lookdev"] is True
    assert assets["怀表"].locked_ref_urls == ["/media/w.png"] and assets["怀表"].visual_prompt == "黄铜怀表"
    assert assets["周叙"].ref_urls == [] and assets["周叙"].meta["inherited_from"] is None
    assert assets["天台"].locked_ref_urls == ["/media/roof.png"]  # not in the plan: kept for reuse


async def test_restyle_relooks_everything(sessionmaker, user_and_project):
    _, pid = user_and_project
    await seed(sessionmaker, pid)
    async with sessionmaker() as s:
        new = await fork.fork_project(s, pid)
    async with sessionmaker() as s:
        await fork.apply_plan(s, new.id, PLAN, RemixSpec(modes=["restyle"], style="赛博朋克"), rhythm={})
    async with sessionmaker() as s:
        a = (await s.execute(select(LookdevAsset).where(LookdevAsset.project_id == new.id,
                                                        LookdevAsset.name == "怀表"))).scalar_one()
    assert a.locked_ref_urls == [] and a.visual_prompt == "LLM 重新描述的怀表"


def test_external_characters_must_be_redesigned():
    assets = compliance.plan_assets(PLAN, external=True)
    lin = next(a for a in assets if a.name == "林晚")
    assert lin.meta["redesign_required"] is True and lin.meta["source"] == "external_video"
    assert lin.ref_urls == [] and lin.locked_ref_urls == [] and lin.face_embedding_url is None
    prop = next(a for a in assets if a.name == "怀表")
    assert "redesign_required" not in prop.meta  # only people carry likeness risk
    own = compliance.plan_assets(PLAN, external=False)
    assert all("redesign_required" not in a.meta for a in own)


def test_redesign_flag_clears_only_after_new_lookdev():
    a = LookdevAsset(asset_type="character", name="x", visual_prompt="v", ref_urls=["/media/old.png"],
                     locked_ref_urls=["/media/old.png"], meta={})
    compliance.require_redesign(a)
    assert a.ref_urls == [] and a.locked_ref_urls == [] and a.meta["redesign_required"] is True
    assert compliance.bindable_refs(a) == []
    with pytest.raises(ValueError, match="重新定妆"):
        compliance.mark_redesigned(a)
    a.locked_ref_urls = ["/media/new.png"]
    compliance.mark_redesigned(a)
    assert a.meta["redesign_required"] is False and compliance.bindable_refs(a) == ["/media/new.png"]
