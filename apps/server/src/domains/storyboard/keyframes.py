"""Per-shot keyframe still (分镜关键帧): one cheap image per shot from the locked look-dev refs, reviewed
(approve / reject) before any video is paid for. Model comes from routing.model_for("keyframe_image")."""
import uuid
from typing import Any, cast, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.gateway.routing import model_for
from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs
from src.domains.storyboard import shot_orchestrator as orch
from src.models import ModelCatalog, Project, Shot

TYPE_LABEL = {"character": "人物", "prop": "道具", "scene": "场景"}
STATUSES = ("none", "pending", "ready", "approved", "rejected")
RATIOS = set(get_args(JobCreateRequest.model_fields["aspect_ratio"].annotation)) - {"adaptive"}


async def keyframe_request(session: AsyncSession, shot: Shot) -> JobCreateRequest:
    project = await session.get(Project, shot.project_id)
    assert project is not None
    stage = model_for("keyframe_image")
    model = await session.get(ModelCatalog, stage.model)
    cap = model.max_refs if model is not None else 3
    # same late binding as the video render (lookdev.refs.refs_for_episode per asset, remix compliance included)
    refs = await orch.bind_refs_late(session, shot, cap=cap)
    images = list(refs.get("images", []))[:cap]
    labels = orch.role_prefix(refs["assets"]) if refs.get("assets") else ""
    prompt = (labels + "\n" if labels else "") + \
        f"{shot.shot_prompt}\n电影分镜关键帧，单帧静态画面，人物与参考图保持一致，画幅 {project.aspect_ratio}。"
    return JobCreateRequest(project_id=shot.project_id, shot_id=shot.id, task_type="image.t2i", model_key=stage.model,
                            prompt=prompt, refs=MultimodalRefs(image_urls=images), aspect_ratio=cast(
                                Any, project.aspect_ratio if project.aspect_ratio in RATIOS else "9:16"))


def keyframe_meta(shot: Shot) -> dict[str, Any]:
    return {"keyframe_shot_id": str(shot.id), "stage": "keyframe_image"}


async def on_keyframe_rendered(session: AsyncSession, shot_id: uuid.UUID, url: str) -> None:
    """Gateway success hook: the new still replaces the old one and waits for review."""
    async with session.begin():
        shot = await session.get(Shot, shot_id, with_for_update=True)
        if shot is not None:
            shot.keyframe_url, shot.keyframe_status = url, "ready"


async def review(session: AsyncSession, shot_id: uuid.UUID, approve: bool) -> Shot:
    async with session.begin():
        shot = await session.get(Shot, shot_id, with_for_update=True)
        if shot is None:
            raise LookupError(shot_id)
        if not shot.keyframe_url or shot.keyframe_status not in ("ready", "approved", "rejected"):
            raise ValueError("这个镜头还没有可审核的关键帧")
        shot.keyframe_status = "approved" if approve else "rejected"
    return shot
