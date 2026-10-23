"""Storyboard -> shots, L0-L3 tier rules, multi-ref role naming and frame-to-frame chaining."""
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import media
from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs
from src.domains.script.script_engine import EpisodeStoryboardOutput
from src.models import Asset, Job, LookdevAsset, Shot, Template

DRAFT_MODEL = "seedance_2_5_draft"  # Seedance 2.5 样片模式 (480p), same look as the master
MASTER_MODEL = "seedance_2_5_spicy"
MAX_IMAGES = 30
log = logging.getLogger(__name__)
TYPE_LABEL = {"character": "人物", "prop": "道具", "scene": "场景"}


def enqueue_local(name: str, *args: Any) -> None:
    from src.queue import QUEUE_LOCAL, celery_app  # lazy: keep domain importable without a broker
    celery_app.send_task(name, args=list(args), queue=QUEUE_LOCAL)


def _bind_refs(names: list[str], assets: dict[str, LookdevAsset]) -> dict[str, Any]:
    images: list[str] = []
    bound: list[dict[str, Any]] = []
    for n in names:
        a = assets.get(n)
        room = MAX_IMAGES - len(images)
        if a is None or not a.ref_urls or room <= 0:
            continue
        urls = list(a.ref_urls)[:room]
        bound.append({"id": str(a.id), "name": a.name, "type": a.asset_type,
                      "range": [len(images) + 1, len(images) + len(urls)]})
        images += urls
    return {"images": images, "videos": [], "audios": [], "assets": bound}


async def import_storyboard(session: AsyncSession, project_id: uuid.UUID, episode_number: int,
                            board: EpisodeStoryboardOutput) -> list[Shot]:
    async with session.begin():
        assets = {a.name: a for a in (await session.execute(
            select(LookdevAsset).where(LookdevAsset.project_id == project_id))).scalars()}
        templates = {t.pose.lower(): t.id for t in (await session.execute(select(Template))).scalars()}
        shots = [Shot(project_id=project_id, episode_number=episode_number, shot_order=s.shot_order,
                      template_id=templates.get(s.suggested_pose_template.lower()), duration=s.duration,
                      shot_prompt=s.five_part_visual_prompt, dialogue_transcript=s.dialogue or None,
                      refs=_bind_refs([*s.character_names, *s.prop_names, s.scene_name], assets))
                 for s in board.shots]
        session.add_all(shots)
    return shots


def role_prefix(bound: list[dict[str, Any]]) -> str:
    def rng(a: int, b: int) -> str:
        return f"{a}" if a == b else f"{a}-{b}"
    return " ".join(f"[参考图{rng(*x['range'])}: {x['name']} {TYPE_LABEL.get(x['type'], x['type'])}]" for x in bound)


def first_frame_for(shot: Shot, prev: Shot | None, chain: bool, tier: str) -> str | None:
    """A first frame only from an approved keyframe, or the previous shot's last frame for a continuous shot.

    Anything else (a hard cut, a keyframe not yet approved) renders in reference-image mode so the character
    refs reach the model: first-frame mode and multi-ref are mutually exclusive (schemas rule 2).
    """
    if shot.keyframe_status == "approved" and shot.keyframe_url:
        return shot.keyframe_url
    if tier == "L2_master" and chain and shot.continuous_from_prev:
        return (prev.last_frame_url if prev else None) or shot.first_frame_url
    return None


async def shot_request(session: AsyncSession, shot_id: uuid.UUID, tier: str, chain: bool = True) -> JobCreateRequest:
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise LookupError(shot_id)
    template = await session.get(Template, shot.template_id) if shot.template_id else None
    prev = (await session.execute(select(Shot).where(
        Shot.project_id == shot.project_id, Shot.episode_number == shot.episode_number,
        Shot.shot_order < shot.shot_order).order_by(Shot.shot_order.desc()).limit(1))).scalar_one_or_none()
    refs = shot.refs or {}
    images = list(refs.get("images", [])) + (list(template.refs) if template else [])
    prompt = shot.shot_prompt
    if refs.get("assets"):
        prompt = f"{role_prefix(refs['assets'])}\n{prompt}"
    if template:
        prompt += f"\n构图参考: {template.default_prompt}"
    base: dict[str, Any] = {"project_id": shot.project_id, "shot_id": shot.id, "task_type": "video.i2v",
                            "prompt": prompt, "negative_prompt": template.negative_prompt if template else None,
                            "refs": MultimodalRefs(image_urls=images[:MAX_IMAGES], video_urls=refs.get("videos", []),
                                                   audio_urls=refs.get("audios", []))}
    first = first_frame_for(shot, prev, chain, tier)
    if tier == "L1_draft":  # 480p draft: verify blocking & camera cheaply before paying for 1080p
        return JobCreateRequest(**base, model_key=DRAFT_MODEL, resolution="480p",
                                duration=min(max(shot.duration, 4), 30), generate_audio=False, return_last_frame=False,
                                first_frame_url=first)
    if tier == "L2_master":
        return JobCreateRequest(**base, model_key=MASTER_MODEL, resolution="1080p",
                                duration=min(max(shot.duration, 4), 30), return_last_frame=True,
                                first_frame_url=first)
    raise ValueError(f"{tier} is not a cloud render tier")


async def on_shot_rendered(session: AsyncSession, shot_id: uuid.UUID, tier: str, last_frame_url: str | None,
                           chain: bool) -> None:
    async with session.begin():
        shot = await session.get(Shot, shot_id, with_for_update=True)
        if shot is None:
            return
        shot.status, shot.render_tier = f"rendered_{tier}", tier
        if last_frame_url:
            shot.last_frame_url = last_frame_url
        if chain and last_frame_url:
            nxt = (await session.execute(select(Shot).where(
                Shot.project_id == shot.project_id, Shot.episode_number == shot.episode_number,
                Shot.shot_order > shot.shot_order).order_by(Shot.shot_order).limit(1))).scalar_one_or_none()
            if nxt is not None and nxt.continuous_from_prev:  # hard cuts keep reference-image mode
                nxt.first_frame_url = last_frame_url


async def animatic_frames(session: AsyncSession, shot_id: uuid.UUID) -> list[dict[str, Any]]:
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise LookupError(shot_id)
    images = (shot.refs or {}).get("images") or []
    if not images and shot.template_id:
        t = await session.get(Template, shot.template_id)
        images = list(t.refs) if t else []
    if not images:
        raise ValueError("shot has no look-dev or template still for an animatic")
    local = media.local_path(images[0])  # our own uploads are '/media/...' URLs, ffmpeg needs the file
    return [{"image": str(local) if local else images[0], "duration": max(shot.duration, 1),
             "subtitle": shot.dialogue_transcript or ""}]


async def next_shot_order(session: AsyncSession, project_id: uuid.UUID, episode: int) -> int:
    n = (await session.execute(select(func.max(Shot.shot_order)).where(
        Shot.project_id == project_id, Shot.episode_number == episode))).scalar()
    return int(n or 0) + 1


async def render_next(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, shot_id: uuid.UUID, tier: str,
                      chain: bool) -> uuid.UUID | None:
    """Episode batch: after one shot finishes, queue the next (chained L2 must wait for the previous last frame)."""
    from src.domains.gateway import router
    from src.domains.gateway.credit_ledger import LedgerError
    from src.domains.gateway.safety import ContentBlocked, check_prompt
    from src.models import ModelCatalog, Project
    async with sm() as s:
        shot = await s.get(Shot, shot_id)
        assert shot is not None
        nxt = (await s.execute(select(Shot).where(
            Shot.project_id == shot.project_id, Shot.episode_number == shot.episode_number,
            Shot.shot_order > shot.shot_order).order_by(Shot.shot_order).limit(1))).scalar_one_or_none()
        if nxt is None:
            return None
        project = await s.get(Project, shot.project_id)
        assert project is not None
        req = await shot_request(s, nxt.id, tier, chain=chain)
        model = await s.get(ModelCatalog, req.model_key)
        assert model is not None
    try:
        check_prompt(req.prompt, project.content_mode, context={"project_id": str(project.id), "shot_id": str(nxt.id)})
        async with sm() as s:
            job = await router.create_job(s, user_id=project.user_id, req=req,
                                          meta={"render_tier": tier, "chain": chain, "batch": True})
    except (ContentBlocked, LedgerError) as e:  # blocked prompt / out of credits: stop the batch quietly
        log.warning("batch stopped at shot %s: %s", nxt.id, e)
        return None
    await router.queue_dispatch(sm, client, job, model.provider)
    return nxt.id


TIER_RANK = {"L3_post": 4, "L2_master": 3, "L1_draft": 2, "L0_animatic": 1}


def clip_file(s3_key: str) -> Path | None:
    """Asset key -> local file: '/media/...' (downloaded vendor output) or a path we rendered ourselves."""
    if s3_key.startswith(media.PREFIX):
        return media.local_path(s3_key)
    p = Path(s3_key)
    return p.resolve() if p.is_file() else None  # 'pending/...' (download failed) -> None


async def best_clips(session: AsyncSession, project_id: uuid.UUID, episode: int) -> dict[uuid.UUID, Path]:
    """Per shot: the highest-tier, newest finished video we hold locally."""
    rows = (await session.execute(
        select(Job.shot_id, Job.request_payload, Asset.s3_key).join(Asset, Asset.job_id == Job.id)
        .join(Shot, Shot.id == Job.shot_id)
        .where(Shot.project_id == project_id, Shot.episode_number == episode, Job.status == "success",
               Asset.kind == "video").order_by(Asset.created_at))).all()
    best: dict[uuid.UUID, tuple[int, Path]] = {}
    for shot_id, payload, key in rows:
        f = clip_file(key)
        rank = TIER_RANK.get(payload.get("render_tier", ""), 0)
        if f is not None and shot_id is not None and rank >= best.get(shot_id, (0, f))[0]:
            best[shot_id] = (rank, f)
    return {k: v[1] for k, v in best.items()}
