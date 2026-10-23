import uuid
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.deps import Http, Session
from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs
from src.models import LookdevAsset, Project, Template

LOOKDEV_MODEL = "seedream_5_pro"
TYPE_HINT = {"character": "人物定妆照，全身正面站姿，纯色浅灰背景，柔和棚拍光，面部清晰，服装道具细节完整",
             "prop": "道具设定图，单件物品居中，纯色浅灰背景，柔和棚拍光，材质与细节清晰",
             "scene": "场景设定图，无人物，电影级构图与光影，空间纵深清晰"}

api = APIRouter(prefix="/api/v1")


class LookdevCreate(BaseModel):
    asset_type: Literal["character", "prop", "scene"]
    name: str = Field(min_length=1, max_length=128)
    role_tag: str | None = None
    visual_prompt: str = Field(min_length=1)
    ref_urls: list[str] = Field(default_factory=list, max_length=10)
    meta: dict[str, Any] = Field(default_factory=dict)
    likeness_consent: bool = False


def asset_view(a: LookdevAsset) -> dict[str, Any]:
    return {"id": str(a.id), "asset_type": a.asset_type, "name": a.name, "role_tag": a.role_tag,
            "visual_prompt": a.visual_prompt, "ref_urls": a.ref_urls, "meta": a.meta}


@api.post("/projects/{project_id}/lookdev", status_code=201)
async def create_asset(project_id: uuid.UUID, body: LookdevCreate, session: Session) -> dict[str, Any]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    if body.asset_type == "character" and body.ref_urls and not body.likeness_consent:
        raise HTTPException(422, "character reference images require likeness_consent "
                                 "(fictional synthetic character or full likeness authorization)")
    meta = {**body.meta, "likeness_consent": body.likeness_consent}
    a = LookdevAsset(project_id=project_id, **body.model_dump(exclude={"likeness_consent", "meta"}), meta=meta)
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


class LookdevPatch(BaseModel):
    visual_prompt: str | None = Field(default=None, min_length=1)
    ref_urls: list[str] | None = Field(default=None, max_length=10)
    likeness_consent: bool | None = None


@api.patch("/lookdev/{asset_id}")
async def patch_asset(asset_id: uuid.UUID, body: LookdevPatch, session: Session) -> dict[str, Any]:
    a = await session.get(LookdevAsset, asset_id)
    if a is None:
        raise HTTPException(404, "look-dev asset not found")
    consent = body.likeness_consent if body.likeness_consent is not None else a.meta.get("likeness_consent", False)
    added = set(body.ref_urls or []) - set(a.ref_urls)  # reordering / removing needs no new declaration
    if a.asset_type == "character" and added and not consent:
        raise HTTPException(422, "character reference images require likeness_consent "
                                 "(fictional synthetic character or full likeness authorization)")
    if body.visual_prompt is not None:
        a.visual_prompt = body.visual_prompt
    if body.ref_urls is not None:
        a.ref_urls = body.ref_urls
    a.meta = {**a.meta, "likeness_consent": consent}
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


@api.post("/lookdev/{asset_id}/generate", status_code=201)
async def generate_ref(asset_id: uuid.UUID, session: Session, bg: BackgroundTasks, http: Http) -> dict[str, Any]:
    """One reference still per call through the gateway (credits, safety, ledger). Result lands in ref_urls."""
    from src.domains.gateway.api import job_view, submit
    a = await session.get(LookdevAsset, asset_id)
    if a is None:
        raise HTTPException(404, "look-dev asset not found")
    p = await session.get(Project, a.project_id)
    assert p is not None
    prompt = f"{a.name}：{a.visual_prompt}。{TYPE_HINT[a.asset_type]}。画幅 {p.aspect_ratio}。"
    req = JobCreateRequest(project_id=a.project_id, task_type="image.t2i", model_key=LOOKDEV_MODEL, prompt=prompt,
                           refs=MultimodalRefs(image_urls=list(a.ref_urls)[:1]))  # keep the look when re-rolling
    job = await submit(req, session, bg, http, meta={"lookdev_id": str(asset_id)})
    return job_view(job)


async def on_lookdev_rendered(session: AsyncSession, asset_id: uuid.UUID, url: str) -> None:
    async with session.begin():
        a = await session.get(LookdevAsset, asset_id, with_for_update=True)
        if a is not None:
            a.ref_urls = [url, *a.ref_urls][:10]  # newest first: shots bind the first ref


@api.get("/projects/{project_id}/lookdev")
async def list_assets(project_id: uuid.UUID, session: Session) -> list[dict[str, Any]]:
    return [asset_view(a) for a in (await session.execute(select(LookdevAsset).where(
        LookdevAsset.project_id == project_id).order_by(LookdevAsset.asset_type, LookdevAsset.created_at))).scalars()]


@api.get("/projects/{project_id}/templates")
async def list_templates(project_id: uuid.UUID, session: Session) -> list[dict[str, Any]]:
    """Zero-token: plain rows. NSFW templates only for uncensored projects."""
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    q = select(Template).order_by(Template.sort_order)
    if p.content_mode != "uncensored_nsfw":
        q = q.where(Template.is_nsfw.is_(False))
    return [{"id": str(t.id), "category": t.category, "relation": t.relation, "pose": t.pose, "scene": t.scene,
             "default_prompt": t.default_prompt, "negative_prompt": t.negative_prompt,
             "recommended_duration": t.recommended_duration, "refs": t.refs, "is_nsfw": t.is_nsfw}
            for t in (await session.execute(q)).scalars()]
