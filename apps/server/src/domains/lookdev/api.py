import hashlib
import os
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.deps import Http, Session
from src.domains.gateway.routing import model_for
from src.domains.gateway.schemas import JobCreateRequest, MultimodalRefs
from src.domains.lookdev.refs import MAX_LOCKED, refs_for_episode
from src.models import Job, LookdevAsset, Project, Template

MAX_CANDIDATES = 12  # ref_urls = reviewable candidates; only locked_ref_urls / meta.looks[].locked feed shots
GEN_CACHE_SIZE = 50
LIVE_JOB = {"queued", "processing", "success"}  # a cache entry pointing at these is reused, never re-submitted
DEFAULT_USD_PER_IMAGE = 0.03  # Seedream estimate (third-party, 2026-10); override with env LOOKDEV_USD_PER_IMAGE
CONSENT_MSG = ("character reference images require likeness_consent "
               "(fictional synthetic character or full likeness authorization)")
TYPE_HINT = {"character": "人物定妆照，全身正面站姿，纯色浅灰背景，柔和棚拍光，面部清晰，服装道具细节完整",
             "prop": "道具设定图，单件物品居中，纯色浅灰背景，柔和棚拍光，材质与细节清晰",
             "scene": "场景设定图，无人物，电影级构图与光影，空间纵深清晰"}
Kind = Literal["portrait", "turnaround", "expressions", "costume"]
KIND_TYPES: dict[str, set[str]] = {"portrait": {"character", "prop", "scene"}, "turnaround": {"character", "prop"},
                                   "expressions": {"character"}, "costume": {"character"}}

api = APIRouter(prefix="/api/v1")


class LookdevCreate(BaseModel):
    asset_type: Literal["character", "prop", "scene"]
    name: str = Field(min_length=1, max_length=128)
    role_tag: str | None = None
    visual_prompt: str = Field(min_length=1)
    ref_urls: list[str] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    meta: dict[str, Any] = Field(default_factory=dict)
    likeness_consent: bool = False


def asset_view(a: LookdevAsset) -> dict[str, Any]:
    return {"id": str(a.id), "asset_type": a.asset_type, "name": a.name, "role_tag": a.role_tag,
            "visual_prompt": a.visual_prompt, "ref_urls": a.ref_urls, "locked_ref_urls": a.locked_ref_urls,
            "meta": {k: v for k, v in a.meta.items() if k != "gen_cache"}}


async def get_asset(session: AsyncSession, asset_id: uuid.UUID, lock: bool = False) -> LookdevAsset:
    a = await session.get(LookdevAsset, asset_id, with_for_update=lock, populate_existing=lock)
    if a is None:
        raise HTTPException(404, "look-dev asset not found")
    return a


@api.post("/projects/{project_id}/lookdev", status_code=201)
async def create_asset(project_id: uuid.UUID, body: LookdevCreate, session: Session) -> dict[str, Any]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    if body.asset_type == "character" and body.ref_urls and not body.likeness_consent:
        raise HTTPException(422, CONSENT_MSG)
    meta = {**body.meta, "likeness_consent": body.likeness_consent}
    a = LookdevAsset(project_id=project_id, **body.model_dump(exclude={"likeness_consent", "meta"}), meta=meta)
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


class LookdevPatch(BaseModel):
    visual_prompt: str | None = Field(default=None, min_length=1)
    ref_urls: list[str] | None = Field(default=None, max_length=MAX_CANDIDATES)
    likeness_consent: bool | None = None


def _prune_locks(a: LookdevAsset) -> None:
    """Locks may only point at current candidates."""
    keep = set(a.ref_urls)
    a.locked_ref_urls = [u for u in a.locked_ref_urls if u in keep]
    looks = a.meta.get("looks")
    if looks:
        a.meta = {**a.meta, "looks": [{**lk, "locked": [u for u in lk.get("locked", []) if u in keep]}
                                      for lk in looks if isinstance(lk, dict)]}


@api.patch("/lookdev/{asset_id}")
async def patch_asset(asset_id: uuid.UUID, body: LookdevPatch, session: Session) -> dict[str, Any]:
    a = await get_asset(session, asset_id)
    consent = body.likeness_consent if body.likeness_consent is not None else a.meta.get("likeness_consent", False)
    added = set(body.ref_urls or []) - set(a.ref_urls)  # reordering / removing needs no new declaration
    if a.asset_type == "character" and added and not consent:
        raise HTTPException(422, CONSENT_MSG)
    if body.visual_prompt is not None:
        a.visual_prompt = body.visual_prompt
    if body.ref_urls is not None:
        a.ref_urls = body.ref_urls
    a.meta = {**a.meta, "likeness_consent": consent}
    _prune_locks(a)
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


# --- lock: the only stills shots bind --------------------------------------------------------------------------

class LockBody(BaseModel):
    urls: list[str] = Field(max_length=MAX_LOCKED)


def _check_candidates(a: LookdevAsset, urls: list[str]) -> None:
    missing = [u for u in urls if u not in a.ref_urls]
    if missing:
        raise HTTPException(422, f"not a candidate of this asset: {missing[0]}")


@api.post("/lookdev/{asset_id}/lock")
async def lock_refs(asset_id: uuid.UUID, body: LockBody, session: Session) -> dict[str, Any]:
    """Replace the asset-level lock (≤3, chosen from ref_urls, order = priority)."""
    a = await get_asset(session, asset_id, lock=True)
    _check_candidates(a, body.urls)
    a.locked_ref_urls = list(dict.fromkeys(body.urls))
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


class UnlockBody(BaseModel):
    urls: list[str] | None = None  # omitted = clear the whole lock


@api.post("/lookdev/{asset_id}/unlock")
async def unlock_refs(asset_id: uuid.UUID, session: Session,
                      body: Annotated[UnlockBody | None, Body()] = None) -> dict[str, Any]:
    a = await get_asset(session, asset_id, lock=True)
    drop = set(body.urls) if body and body.urls is not None else set(a.locked_ref_urls)
    a.locked_ref_urls = [u for u in a.locked_ref_urls if u not in drop]
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


# --- per-episode looks (costume changes) -----------------------------------------------------------------------

class LookBody(BaseModel):
    name: str | None = None
    episodes: list[int] = Field(min_length=2, max_length=2)
    locked: list[str] = Field(default_factory=list, max_length=MAX_LOCKED)


@api.put("/lookdev/{asset_id}/looks/{name}")
async def save_look(asset_id: uuid.UUID, name: str, body: LookBody, session: Session) -> dict[str, Any]:
    a = await get_asset(session, asset_id, lock=True)
    start, end = body.episodes
    if start < 1 or end < start:
        raise HTTPException(422, "episodes must be [start, end] with 1 <= start <= end")
    _check_candidates(a, body.locked)
    look = {"name": name, "episodes": [start, end], "locked": list(dict.fromkeys(body.locked))}
    looks = [lk for lk in a.meta.get("looks") or [] if isinstance(lk, dict) and lk.get("name") != name]
    a.meta = {**a.meta, "looks": [*looks, look]}
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


@api.delete("/lookdev/{asset_id}/looks/{name}")
async def delete_look(asset_id: uuid.UUID, name: str, session: Session) -> dict[str, Any]:
    a = await get_asset(session, asset_id, lock=True)
    a.meta = {**a.meta, "looks": [lk for lk in a.meta.get("looks") or []
                                  if isinstance(lk, dict) and lk.get("name") != name]}
    await session.commit()
    await session.refresh(a)
    return asset_view(a)


@api.get("/lookdev/{asset_id}/refs_for_episode")
async def get_refs_for_episode(asset_id: uuid.UUID, session: Session, episode: int = Query(1, ge=1),
                               n: int = Query(MAX_LOCKED, ge=1, le=MAX_LOCKED)) -> dict[str, Any]:
    a = await get_asset(session, asset_id)
    return {"asset_id": str(a.id), "episode": episode, "urls": (await refs_for_episode(session, a, episode))[:n]}


# --- generation: kinds, references, dedup, estimate ------------------------------------------------------------

class GenerateBody(BaseModel):
    kind: Kind = "portrait"
    note: str | None = Field(default=None, max_length=500)  # extra direction; required for costume
    force: bool = False  # skip the dedup cache (deliberate re-roll)


def build_prompt(a: LookdevAsset, aspect: str, kind: str, note: str | None) -> str:
    head = f"{a.name}：{a.visual_prompt}。"
    if kind == "turnaround":
        body = ("三视图设定拼版：同一张横版图内从左到右并排正面、侧面、背面全身，三者服装发型比例完全一致，"
                "纯色浅灰背景，柔和棚拍光，画幅 16:9。")
    elif kind == "expressions":
        body = ("表情四宫格：一张图内 2×2 网格，同一角色胸像，依次为平静、喜悦、愤怒、悲伤，脸型发型服装完全一致，"
                "纯色浅灰背景，画幅 1:1。")
    elif kind == "costume":
        body = f"换装定妆：保持面部、发型与体型完全一致，服装改为：{note}。全身正面站姿，纯色浅灰背景，画幅 {aspect}。"
        return head + body
    else:
        body = f"{TYPE_HINT[a.asset_type]}。画幅 {aspect}。"
    return head + body + (f"补充：{note}。" if note else "")


def gen_refs(a: LookdevAsset) -> list[str]:
    """Locked stills keep the look. Without a lock, a user-uploaded still (never a render) may anchor it, so an
    unreviewed render can never feed the next one."""
    if a.locked_ref_urls:
        return [str(u) for u in a.locked_ref_urls][:MAX_LOCKED]
    return [str(u) for u in a.ref_urls if not str(u).startswith("/media/renders/")][:1]


def gen_key(model: str, prompt: str, refs: list[str]) -> str:
    return hashlib.sha256("\n".join([model, prompt, *sorted(refs)]).encode()).hexdigest()


def usd_per_image() -> float:
    return float(os.environ.get("LOOKDEV_USD_PER_IMAGE", DEFAULT_USD_PER_IMAGE))


async def _plan(session: AsyncSession, a: LookdevAsset, kind: str, note: str | None) -> tuple[str, str, list[str]]:
    if a.asset_type not in KIND_TYPES[kind]:
        raise HTTPException(422, f"{kind} is not available for {a.asset_type}")
    if kind == "costume" and not (note and note.strip()):
        raise HTTPException(422, "costume needs a note describing the outfit")
    p = await session.get(Project, a.project_id)
    assert p is not None
    model = model_for("lookdev_image").model
    refs = gen_refs(a)
    return model, build_prompt(a, p.aspect_ratio, kind, note), refs


async def _cached_job(session: AsyncSession, a: LookdevAsset, key: str) -> Job | None:
    job_id = (a.meta.get("gen_cache") or {}).get(key)
    if not job_id:
        return None
    job = await session.get(Job, uuid.UUID(job_id))
    return job if job is not None and job.status in LIVE_JOB else None


@api.post("/lookdev/{asset_id}/generate", status_code=201)
async def generate_ref(asset_id: uuid.UUID, session: Session, bg: BackgroundTasks, http: Http, response: Response,
                       body: Annotated[GenerateBody | None, Body()] = None) -> dict[str, Any]:
    """One image per call through the gateway (credits, safety, ledger). Lands in ref_urls as a candidate.
    Same model+prompt+refs as a live/successful earlier job returns that job (200, cached) instead of paying again."""
    from src.domains.gateway.api import job_view, submit
    b = body or GenerateBody()
    a = await get_asset(session, asset_id)
    model, prompt, refs = await _plan(session, a, b.kind, b.note)
    key = gen_key(model, prompt, refs)
    if not b.force and (hit := await _cached_job(session, a, key)) is not None:
        response.status_code = 200
        return {**job_view(hit), "cached": True, "kind": b.kind}
    req = JobCreateRequest(project_id=a.project_id, task_type="image.t2i", model_key=model, prompt=prompt,
                           refs=MultimodalRefs(image_urls=refs))
    job = await submit(req, session, bg, http, meta={"lookdev_id": str(asset_id), "lookdev_kind": b.kind})
    view = job_view(job)
    a = await get_asset(session, asset_id, lock=True)
    cache = {k: v for k, v in (a.meta.get("gen_cache") or {}).items() if k != key}
    cache[key] = view["id"]
    a.meta = {**a.meta, "gen_cache": dict(list(cache.items())[-GEN_CACHE_SIZE:])}
    await session.commit()
    return {**view, "cached": False, "kind": b.kind}


@api.get("/lookdev/{asset_id}/estimate")
async def estimate(asset_id: uuid.UUID, session: Session, kind: Kind = "portrait",
                   note: str | None = None) -> dict[str, Any]:
    """Zero-token cost preview: every kind is exactly one image call; a dedup hit costs nothing."""
    a = await get_asset(session, asset_id)
    model, prompt, refs = await _plan(session, a, kind, note)
    cached = await _cached_job(session, a, gen_key(model, prompt, refs)) is not None
    return {"kind": kind, "images": 1, "usd": 0 if cached else round(usd_per_image(), 4), "cached": cached}


async def on_lookdev_rendered(session: AsyncSession, asset_id: uuid.UUID, url: str) -> None:
    """A finished render is a *candidate*: newest first, ≤12, never auto-locked. Trimming drops the oldest
    candidates that no lock (asset or per-episode look) points at."""
    async with session.begin():
        a = await session.get(LookdevAsset, asset_id, with_for_update=True)
        if a is None:
            return
        pinned = set(a.locked_ref_urls) | {u for lk in a.meta.get("looks") or [] if isinstance(lk, dict)
                                           for u in lk.get("locked", [])}
        refs = [url, *(u for u in a.ref_urls if u != url)]
        for i in range(len(refs) - 1, 0, -1):
            if len(refs) <= MAX_CANDIDATES:
                break
            if refs[i] not in pinned:
                del refs[i]
        a.ref_urls = refs[:MAX_CANDIDATES] if len(refs) > MAX_CANDIDATES else refs


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
