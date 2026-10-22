import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.deps import Session
from src.models import LookdevAsset, Project, Template

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
