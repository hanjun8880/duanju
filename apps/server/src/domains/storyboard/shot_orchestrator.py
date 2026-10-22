"""Storyboard -> shots, L0-L3 tier rules, multi-ref role naming and frame-to-frame chaining."""
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs
from src.domains.script.script_engine import EpisodeStoryboardOutput
from src.models import LookdevAsset, Shot, Template

DRAFT_MODEL = "minimax_h3"
MASTER_MODEL = "seedance_2_5_spicy"
MAX_IMAGES = 30
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
    if tier == "L1_draft":  # 480p 4-5s draft: verify blocking & camera cheaply
        return JobCreateRequest(**base, model_key=DRAFT_MODEL, resolution="480p",
                                duration=min(max(shot.duration, 4), 5), generate_audio=False, return_last_frame=False)
    if tier == "L2_master":
        first = shot.first_frame_url or (prev.last_frame_url if prev else None)
        return JobCreateRequest(**base, model_key=MASTER_MODEL, resolution="1080p",
                                duration=min(max(shot.duration, 4), 30), return_last_frame=True,
                                first_frame_url=first if chain else None)
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
            if nxt is not None:
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
    return [{"image": images[0], "duration": max(shot.duration, 1), "subtitle": shot.dialogue_transcript or ""}]


async def next_shot_order(session: AsyncSession, project_id: uuid.UUID, episode: int) -> int:
    n = (await session.execute(select(func.max(Shot.shot_order)).where(
        Shot.project_id == project_id, Shot.episode_number == episode))).scalar()
    return int(n or 0) + 1
