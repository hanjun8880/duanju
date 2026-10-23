"""One-click fork of an own project: bible, episodes, locked looks (refs + voice) and establishing plates are
copied, never regenerated. Every copy records meta.inherited_from. apply_plan then folds a remix SeriesPlan in
while keeping every locked look the chosen modes do not change."""
import copy
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.remix.modes import RemixSpec
from src.domains.script import series
from src.domains.script.script_engine import SeriesPlan
from src.domains.storyboard import shot_orchestrator as orch
from src.models import Episode, LookdevAsset, Project, Shot


async def fork_project(session: AsyncSession, source_id: uuid.UUID, title: str | None = None) -> Project:
    async with session.begin():
        src = await session.get(Project, source_id)
        if src is None:
            raise LookupError(f"project {source_id} not found")
        new = Project(user_id=src.user_id, title=title or f"{src.title}（二创）", category="remix",
                      content_mode=src.content_mode, aspect_ratio=src.aspect_ratio,
                      series_bible_json={**copy.deepcopy(src.series_bible_json), "inherited_from": str(src.id)},
                      source_novel_text=src.source_novel_text, status=src.status, budget_usd=src.budget_usd)
        session.add(new)
        await session.flush()
        for e in (await session.execute(select(Episode).where(Episode.project_id == source_id))).scalars():
            session.add(Episode(project_id=new.id, episode_number=e.episode_number, title=e.title,
                                synopsis=e.synopsis, source_segments=list(e.source_segments),
                                script_text=e.script_text, status=e.status, budget_usd=e.budget_usd))
        for a in (await session.execute(select(LookdevAsset).where(LookdevAsset.project_id == source_id))).scalars():
            session.add(LookdevAsset(
                project_id=new.id, asset_type=a.asset_type, name=a.name, role_tag=a.role_tag,
                visual_prompt=a.visual_prompt, ref_urls=list(a.ref_urls), face_embedding_url=a.face_embedding_url,
                locked_ref_urls=list(a.locked_ref_urls), voice=copy.deepcopy(a.voice),
                meta={**copy.deepcopy(a.meta), "inherited_from": {"project_id": str(source_id), "asset_id": str(a.id)}}))
        plates = (await session.execute(select(Shot).where(Shot.project_id == source_id,
                                                           Shot.shot_kind == "establishing"))).scalars().all()
        clips: dict[uuid.UUID, Any] = {}
        for ep in {s.episode_number for s in plates}:
            clips |= await orch.best_clips(session, source_id, ep)
        for s in plates:
            clip = clips.get(s.id)
            session.add(Shot(
                project_id=new.id, episode_number=s.episode_number, shot_order=s.shot_order, duration=s.duration,
                resolution=s.resolution, render_tier=s.render_tier, shot_prompt=s.shot_prompt,
                refs=copy.deepcopy(s.refs), first_frame_url=s.first_frame_url, last_frame_url=s.last_frame_url,
                keyframe_url=s.keyframe_url, keyframe_status=s.keyframe_status, shot_kind=s.shot_kind,
                content_hash=s.content_hash, status=s.status,
                source_remix_meta={**(s.source_remix_meta or {}),
                                   "inherited_from": {"project_id": str(source_id), "shot_id": str(s.id)},
                                   "clip_path": str(clip) if clip else None}))
    return new


def _relook(spec: RemixSpec, asset_type: str, name: str) -> bool:
    if "restyle" in spec.modes:
        return True
    if "recast" in spec.modes and asset_type == "character":
        return not spec.recast or name in spec.recast
    return False


async def apply_plan(session: AsyncSession, project_id: uuid.UUID, plan: SeriesPlan, spec: RemixSpec,
                     rhythm: dict[str, Any] | None = None) -> None:
    async with session.begin():
        p = await session.get(Project, project_id, with_for_update=True)
        if p is None:
            raise LookupError(f"project {project_id} not found")
        old = p.series_bible_json or {}
        bible = {**series.bible_from_plan(plan), "remix": {"modes": list(spec.modes)}}
        if old.get("inherited_from"):
            bible["inherited_from"] = old["inherited_from"]
        r = rhythm if rhythm else old.get("rhythm")
        if r is not None:
            bible["rhythm"] = r
        p.series_bible_json, p.status = bible, "planned"
        await session.execute(delete(Episode).where(Episode.project_id == project_id))
        session.add_all([Episode(project_id=project_id, episode_number=o.episode_number, title=o.title,
                                 synopsis=o.synopsis, source_segments=list(o.source_segments))
                         for o in plan.episodes])
        have = {a.name: a for a in (await session.execute(select(LookdevAsset).where(
            LookdevAsset.project_id == project_id))).scalars()}
        cards = [("character", c.name, c.role_tag, c.visual_prompt) for c in plan.characters] + \
            [("prop", x.name, None, x.visual_prompt) for x in plan.props] + \
            [("scene", x.name, None, x.visual_prompt) for x in plan.scenes]
        for t, name, role, prompt in cards:
            a = have.get(name)
            if a is None:
                session.add(LookdevAsset(project_id=project_id, asset_type=t, name=name, role_tag=role,
                                         visual_prompt=prompt, meta={"likeness_consent": False, "from_plan": True,
                                                                     "inherited_from": None}))
            elif _relook(spec, a.asset_type, name):
                a.visual_prompt, a.ref_urls, a.locked_ref_urls, a.face_embedding_url = prompt, [], [], None
                a.meta = {**a.meta, "needs_lookdev": True}
            # else: the inherited locked look wins over the model's re-description
