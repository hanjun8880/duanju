"""Storyboard -> shots, L0-L3 tier rules, multi-ref role naming and frame-to-frame chaining."""
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import media
from src.config import settings
from src.domains.gateway.routing import model_for, video_stage_for
from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs
from src.domains.lookdev.refs import refs_for_episode
from src.domains.script.script_engine import EpisodeStoryboardOutput
from src.models import Asset, Job, LookdevAsset, Shot, Template

MAX_IMAGES = 30  # model/resolution per tier and shot kind come from gateway/routing.py
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
        speakers = {n: a.id for n, a in assets.items() if a.asset_type == "character"}
        shots = [Shot(project_id=project_id, episode_number=episode_number, shot_order=s.shot_order,
                      template_id=templates.get(s.suggested_pose_template.lower()), duration=s.duration,
                      shot_prompt=s.five_part_visual_prompt, dialogue_transcript=s.dialogue or None,
                      shot_kind=s.shot_kind, continuous_from_prev=s.continuous_from_prev and i > 0,
                      speaker_asset_id=speakers.get(s.speaker.strip()),
                      refs={**_bind_refs(names, assets), "asset_names": names})
                 for i, s in enumerate(board.shots) for names in ([*s.character_names, *s.prop_names, s.scene_name],)]
        session.add_all(shots)
    return shots


REF_ORDER = ("character", "scene", "prop")  # then the pose template's stills


async def bind_refs_late(session: AsyncSession, shot: Shot, cap: int = MAX_IMAGES) -> dict[str, Any]:
    """Resolve refs at render time from the current look-dev assets (so locking after import takes effect).
    Per asset: lookdev.refs.refs_for_episode (episode look > lock > first candidate; nothing while a remix
    redesign is pending). Shared by video renders and keyframe stills."""
    snap = shot.refs or {}
    names = snap.get("asset_names")
    if names is None:
        names = [a["name"] for a in snap.get("assets", [])]
    if not names:  # remix / hand-made shots carry their own refs
        return snap
    rows = (await session.execute(select(LookdevAsset).where(
        LookdevAsset.project_id == shot.project_id, LookdevAsset.name.in_(names)))).scalars().all()
    rank = {t: i for i, t in enumerate(REF_ORDER)}
    rows = sorted(rows, key=lambda a: (rank.get(a.asset_type, len(REF_ORDER)), names.index(a.name)))
    images: list[str] = []
    bound: list[dict[str, Any]] = []
    for a in rows:
        room = cap - len(images)
        urls = (await refs_for_episode(session, a, shot.episode_number))[:max(room, 0)]
        if not urls:
            continue
        bound.append({"id": str(a.id), "name": a.name, "type": a.asset_type,
                      "range": [len(images) + 1, len(images) + len(urls)]})
        images += urls
    return {"images": images, "videos": snap.get("videos", []), "audios": snap.get("audios", []), "assets": bound}


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


async def shot_request(session: AsyncSession, shot_id: uuid.UUID, tier: str, chain: bool = True,
                       final: bool = False) -> JobCreateRequest:
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise LookupError(shot_id)
    template = await session.get(Template, shot.template_id) if shot.template_id else None
    prev = (await session.execute(select(Shot).where(
        Shot.project_id == shot.project_id, Shot.episode_number == shot.episode_number,
        Shot.shot_order < shot.shot_order).order_by(Shot.shot_order.desc()).limit(1))).scalar_one_or_none()
    refs = await bind_refs_late(session, shot)
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
    if tier == "L1_draft":  # 480p draft: verify blocking & camera cheaply before paying for the master
        stage = model_for(video_stage_for(shot.shot_kind, draft=True))
        res: Any = stage.resolution or "480p"  # validated by JobCreateRequest's Literal
        return JobCreateRequest(**base, model_key=stage.model, resolution=res,
                                duration=min(max(shot.duration, 4), 30), generate_audio=False, return_last_frame=False,
                                first_frame_url=first)
    if tier == "L2_master":  # 720p per shot kind; 1080p only on an explicit final render
        stage = model_for(video_stage_for(shot.shot_kind, final=final))
        res = stage.resolution or "720p"
        return JobCreateRequest(**base, model_key=stage.model, resolution=res,
                                duration=min(max(shot.duration, 4), 30), return_last_frame=True,
                                first_frame_url=first,
                                # vendor voices differ shot to shot: silent clips + character TTS unless enabled
                                generate_audio=settings.video_native_audio)
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
                      chain: bool, final: bool = False, force: bool = False) -> uuid.UUID | None:
    """Episode batch: after one shot finishes, queue the next (chained L2 must wait for the previous last frame)."""
    async with sm() as s:
        shot = await s.get(Shot, shot_id)
        assert shot is not None
        nxt = (await s.execute(select(Shot).where(
            Shot.project_id == shot.project_id, Shot.episode_number == shot.episode_number,
            Shot.shot_order > shot.shot_order).order_by(Shot.shot_order).limit(1))).scalar_one_or_none()
        if nxt is None:
            return None
        nxt_id = nxt.id
    return await _queue_shot(sm, client, nxt_id, tier, chain, final, force)


async def _queue_shot(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, shot_id: uuid.UUID, tier: str,
                      chain: bool, final: bool, force: bool = False) -> uuid.UUID | None:
    from src.domains.gateway import router
    from src.domains.gateway.credit_ledger import LedgerError
    from src.domains.gateway.safety import ContentBlocked, check_prompt
    from src.models import ModelCatalog, Project
    async with sm() as s:
        shot = await s.get(Shot, shot_id)
        assert shot is not None
        project = await s.get(Project, shot.project_id)
        assert project is not None
        req = await shot_request(s, shot_id, tier, chain=chain, final=final)
        model = await s.get(ModelCatalog, req.model_key)
        assert model is not None
    try:
        check_prompt(req.prompt, project.content_mode, context={"project_id": str(project.id), "shot_id": str(shot_id)})
    except ContentBlocked as e:  # this shot can't render: skip its continuous run, carry on at the next cut
        log.warning("batch: shot %s blocked: %s", shot_id, e)
        return await on_render_failed(sm, client, shot_id, tier, chain=chain, final=final, batch=True, force=force)
    try:
        async with sm() as s:
            job = await router.create_job(s, user_id=project.user_id, req=req, force=force,
                                          meta={"render_tier": tier, "chain": chain, "batch": True, "final": final})
    except LedgerError as e:  # out of credits / over budget: stop the batch quietly
        log.warning("batch stopped at shot %s: %s", shot_id, e)
        return None
    if job.status == "success":  # content-hash cache hit: no vendor, no callback -> advance the batch here
        await router.run_cached_followups(sm, client, job)
    else:
        await router.queue_dispatch(sm, client, job, model.provider)
    return shot_id


async def on_render_failed(sm: async_sessionmaker[AsyncSession], client: httpx.AsyncClient,
                           shot_id: uuid.UUID, tier: str, chain: bool, final: bool = False,
                           batch: bool = False, force: bool = False) -> uuid.UUID | None:
    """Mark a shot failed. In a batch, the continuous shots after it (they need its last frame) fail too, and the
    batch resumes at the next non-continuous shot instead of stopping the whole episode."""
    async with sm() as s, s.begin():
        shot = await s.get(Shot, shot_id, with_for_update=True)
        if shot is None:
            return None
        shot.status = "failed"
        if not batch:
            return None
        later = (await s.execute(select(Shot).where(
            Shot.project_id == shot.project_id, Shot.episode_number == shot.episode_number,
            Shot.shot_order > shot.shot_order).order_by(Shot.shot_order))).scalars().all()
        resume = None
        for nxt in later:
            if chain and nxt.continuous_from_prev:
                nxt.status = "failed"
                continue
            resume = nxt.id
            break
    if resume is None:
        return None
    return await _queue_shot(sm, client, resume, tier, chain, final, force)


TIER_RANK = {"L3_post": 4, "L2_master": 3, "L1_draft": 2, "L0_animatic": 1}
# Jobs that can produce a shot take. The jobs table also holds llm.* rows (no shot), tts rows and keyframe stills
# (image.t2i on a shot): none of those is a take.
RENDER_TASK = or_(Job.task_type.like("video.%"), Job.task_type.like("post.%"), Job.task_type == "face.swap")


def is_render_task(task_type: str) -> bool:
    return task_type.startswith(("video.", "post.")) or task_type == "face.swap"


def clip_file(s3_key: str) -> Path | None:
    """Asset key -> local file: '/media/...' (downloaded vendor output) or a path we rendered ourselves."""
    if s3_key.startswith(media.PREFIX):
        return media.local_path(s3_key)
    p = Path(s3_key)
    return p.resolve() if p.is_file() else None  # 'pending/...' (download failed) -> None


async def best_clips(session: AsyncSession, project_id: uuid.UUID, episode: int) -> dict[uuid.UUID, Path]:
    """Per shot: the take the director selected, else the highest-tier, newest finished video we hold locally."""
    rows = (await session.execute(
        select(Job.shot_id, Job.request_payload, Asset.s3_key, Job.id, Shot.selected_job_id)
        .join(Asset, Asset.job_id == Job.id).join(Shot, Shot.id == Job.shot_id)
        .where(Shot.project_id == project_id, Shot.episode_number == episode, Job.status == "success",
               Asset.kind == "video", RENDER_TASK).order_by(Asset.created_at))).all()
    best: dict[uuid.UUID, tuple[int, Path]] = {}
    for shot_id, payload, key, job_id, selected in rows:
        f = clip_file(key)
        rank = 99 if job_id == selected else TIER_RANK.get(payload.get("render_tier", ""), 0)
        if f is not None and shot_id is not None and rank >= best.get(shot_id, (0, f))[0]:
            best[shot_id] = (rank, f)
    return {k: v[1] for k, v in best.items()}


async def shot_takes(session: AsyncSession, shot_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Every finished video take per shot (newest first), for the director to compare and select."""
    if not shot_ids:
        return {}
    rows = (await session.execute(
        select(Job.shot_id, Job.id, Job.model_key, Job.request_payload, Asset.s3_key, Asset.created_at)
        .join(Asset, Asset.job_id == Job.id)
        .where(Job.shot_id.in_(shot_ids), Job.status == "success", Asset.kind == "video", RENDER_TASK)
        .order_by(Asset.created_at.desc()))).all()
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for shot_id, job_id, model_key, payload, key, created_at in rows:
        f = clip_file(key)
        url = media.url_for(f) if f is not None and media.root() in f.parents else None
        if shot_id is None or url is None:
            continue
        out.setdefault(shot_id, []).append({
            "job_id": str(job_id), "render_tier": payload.get("render_tier"), "model_key": model_key,
            "final": bool(payload.get("final")), "url": url, "created_at": created_at.isoformat()})
    return out
