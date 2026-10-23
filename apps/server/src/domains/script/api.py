import asyncio
import contextlib
import os
import shutil
import uuid
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.deps import Http, Session, director
from src.domains.script import series, source_import, usage
from src.domains.script.script_engine import (
    CachedDirectorLLM,
    EpisodeScript,
    EpisodeStoryboardOutput,
    LLMRefused,
    SeriesPlan,
)
from src.domains.storyboard import shot_orchestrator as orch
from src.models import Episode, Job, LookdevAsset, Project, Shot

api = APIRouter(prefix="/api/v1")

DIRECTOR_RULES = (
    "你是连续剧分镜导演。按短剧节奏拆镜：前3秒强冲突钩子、中段情绪递进、结尾悬念卡点。"
    "每镜 4-8 秒为主，最长 10 秒；一段连续动作拆成几镜时，后续镜头标 continuous_from_prev=true（接上一镜尾帧）。"
    "shot_kind 标 dialogue/action/establishing/insert；有台词时 speaker 填说话角色名。"
    "five_part_visual_prompt 必须依次写：1起始构图 2摄影机运动 "
    "3主体连续动作与交互 4环境光影变化 5结束定格。角色、道具、场景名必须与 SERIES_BIBLE 中的名称完全一致。"
    "所有角色均为虚构成年人。"
)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: Literal["series", "remix", "short"] = "series"
    content_mode: Literal["standard_sfw", "uncensored_nsfw"] = "standard_sfw"
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"] = "9:16"
    source_novel_text: str | None = None
    confirm_18plus: bool = False


def project_view(p: Project) -> dict[str, Any]:
    bible = {k: v for k, v in (p.series_bible_json or {}).items() if not k.startswith("_")}  # no digest cache
    return {"id": str(p.id), "title": p.title, "category": p.category, "content_mode": p.content_mode,
            "aspect_ratio": p.aspect_ratio, "status": p.status, "series_bible_json": bible,
            "has_source_text": bool(p.source_novel_text), "created_at": p.created_at.isoformat()}


@api.post("/projects", status_code=201)
async def create_project(body: ProjectCreate, session: Session) -> dict[str, Any]:
    user = await director(session)
    if body.content_mode == "uncensored_nsfw":
        if not body.confirm_18plus:
            raise HTTPException(422, "uncensored_nsfw requires confirm_18plus: operator must be 18+")
        user.age_verified_18plus = True
    p = Project(user_id=user.id, **body.model_dump(exclude={"confirm_18plus"}))
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return project_view(p)


@api.get("/projects")
async def list_projects(session: Session) -> list[dict[str, Any]]:
    return [project_view(p) for p in (await session.execute(
        select(Project).order_by(Project.created_at.desc()))).scalars()]


@api.get("/projects/{project_id}")
async def get_project(project_id: uuid.UUID, session: Session) -> dict[str, Any]:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return {**project_view(p), "source_novel_text": p.source_novel_text}


class StoryboardImport(BaseModel):
    episode_number: int = Field(default=1, ge=1)
    storyboard: EpisodeStoryboardOutput


@api.post("/projects/{project_id}/storyboard", status_code=201)
async def import_storyboard(project_id: uuid.UUID, body: StoryboardImport, session: Session) -> dict[str, Any]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    await _replaceable_shots(session, project_id, body.episode_number)
    await session.close()
    shots = await orch.import_storyboard(session, project_id, body.episode_number, body.storyboard)
    return {"created": len(shots)}


class CompileRequest(BaseModel):
    episode_number: int = Field(default=1, ge=1)
    text: str = Field(min_length=1)


@api.post("/projects/{project_id}/storyboard/compile", status_code=201)
async def compile_storyboard(project_id: uuid.UUID, body: CompileRequest, session: Session) -> dict[str, Any]:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    _need_llm(p)
    mode, bible = p.content_mode, p.series_bible_json
    await _replaceable_shots(session, project_id, body.episode_number)
    await session.close()
    async with _ledgered(project_id) as llm:
        board = await _llm(llm.compile_episode_storyboard(DIRECTOR_RULES, bible, body.text, content_mode=mode))
    shots = await orch.import_storyboard(session, project_id, body.episode_number, board)
    return {"created": len(shots), "episode_title": board.episode_title, "hook_summary": board.hook_summary}


@contextlib.asynccontextmanager
async def _ledgered(project_id: uuid.UUID) -> AsyncIterator[CachedDirectorLLM]:
    """One LLM client per request; every call it made (even refused/truncated ones) becomes a jobs row."""
    llm = CachedDirectorLLM()
    try:
        yield llm
    finally:
        await usage.record_llm_calls(project_id, llm.calls)


@api.get("/projects/{project_id}/llm-usage")
async def llm_usage(project_id: uuid.UUID, session: Session) -> dict[str, Any]:
    """LLM spend for this project, from the llm.* job rows (USD is an estimate from settings.llm_prices)."""
    return await usage.project_spend(session, project_id)


def _need_llm(p: Project) -> None:
    if p.content_mode == "standard_sfw" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured on the server")


def _tier(p: Project, stage: str, explicit: str | None) -> str | None:
    """A per-call tier wins; otherwise the project's saved default for that stage (routing.QUALITY_TIERS)."""
    if explicit:
        return explicit
    return ((p.series_bible_json or {}).get("_quality_tiers") or {}).get(stage)


@api.get("/stage-tiers")
async def stage_tiers() -> dict[str, dict[str, dict[str, str]]]:
    """Every stage that offers a draft/standard/high choice, for the frontend's quality dropdowns."""
    from src.domains.gateway import routing
    return {s: routing.tiers_for(s) for s in routing.QUALITY_TIERS}


class QualityTiers(BaseModel):
    tiers: dict[str, str] = Field(default_factory=dict, description="stage -> draft|standard|high")


@api.put("/projects/{project_id}/quality-tiers")
async def set_quality_tiers(project_id: uuid.UUID, body: QualityTiers, session: Session) -> dict[str, Any]:
    from src.domains.gateway import routing
    bad = [s for s in body.tiers if s not in routing.STAGES]
    if bad:
        raise HTTPException(422, f"unknown stage: {bad[0]}")
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    p.series_bible_json = {**(p.series_bible_json or {}), "_quality_tiers": body.tiers}
    await session.commit()
    return {"tiers": body.tiers}


async def _llm[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except LLMRefused as e:
        raise HTTPException(422, f"model declined: {e}") from e
    except ValueError as e:  # truncated output / schema mismatch
        raise HTTPException(422, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


async def _replaceable_shots(session: AsyncSession, project_id: uuid.UUID, episode: int) -> None:
    """Re-storyboarding replaces an episode's shots, unless any of them already has a render job."""
    ids = (await session.execute(select(Shot.id).where(Shot.project_id == project_id,
                                                       Shot.episode_number == episode))).scalars().all()
    if not ids:
        return
    # only render jobs count: llm/tts/keyframe rows don't make a shot "rendered" (jobs.shot_id is SET NULL on delete)
    if (await session.execute(select(Job.id).where(Job.shot_id.in_(ids), orch.RENDER_TASK).limit(1))).first():
        raise HTTPException(409, f"episode {episode} already has rendered shots; edit them instead of replacing")
    await session.execute(delete(Shot).where(Shot.id.in_(ids)))
    await session.commit()


def _save(file: UploadFile, path: Path) -> None:
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)


@api.post("/uploads", status_code=201)
async def upload(file: UploadFile) -> dict[str, str]:
    """Local media store served at /media. shortcut: no MinIO/presigned URLs yet; swap for S3 when deployed."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".wav", ".mp3", ".m4a"}:
        raise HTTPException(422, f"unsupported file type {ext}")
    name = f"{uuid.uuid4().hex}{ext}"
    dest = Path(settings.storage_dir) / "uploads"
    dest.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_save, file, dest / name)
    return {"url": f"/media/uploads/{name}", "path": str(dest / name)}


@api.post("/source/extract-file")
async def extract_file(file: UploadFile) -> dict[str, str]:
    """TXT/PDF/DOCX -> plain text, for the 导入文件 button. No project yet, so nothing is stored here."""
    try:
        text = source_import.extract_file_text(file.filename or "", await file.read())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not text.strip():
        raise HTTPException(422, "文件里没有可提取的文字")
    return {"text": text}


class FetchUrlRequest(BaseModel):
    url: str


@api.post("/source/fetch-url")
async def fetch_url(body: FetchUrlRequest, client: Http) -> dict[str, str]:
    """Webpage -> main body text, for the 抓取正文 button. Only this page, no auto-pagination."""
    try:
        text = await source_import.fetch_url_text(client, body.url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        raise HTTPException(422, f"抓取失败：{e}") from e
    return {"text": text}


class RemixRequest(BaseModel):
    video_path: str


@api.post("/projects/{project_id}/remix", status_code=202)
async def remix(project_id: uuid.UUID, body: RemixRequest, session: Session) -> dict[str, str]:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    root = Path(settings.storage_dir).resolve()
    src = Path(body.video_path).resolve()
    if root not in src.parents or not src.is_file():  # only files we stored ourselves
        raise HTTPException(422, "video_path must be a file returned by /uploads")
    p.status = "remixing"
    await session.commit()
    orch.enqueue_local("remix.deconstruct", str(project_id), str(src))
    return {"queue": "local_post", "status": "queued"}



class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    source_novel_text: str | None = None
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"] | None = None


@api.patch("/projects/{project_id}")
async def patch_project(project_id: uuid.UUID, body: ProjectPatch, session: Session) -> dict[str, Any]:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    await session.commit()
    await session.refresh(p)
    return project_view(p)


def episode_view(e: Episode) -> dict[str, Any]:
    return {"episode_number": e.episode_number, "title": e.title, "synopsis": e.synopsis,
            "source_segments": e.source_segments, "script_text": e.script_text, "status": e.status}


class PlanRequest(BaseModel):
    episode_count: int | None = Field(default=None, ge=1, le=200)
    direction: str | None = Field(default=None, max_length=500, description="创作方向：类型、剧情偏好等，可选")
    replace: bool = False
    tier: Literal["draft", "standard", "high", "overseas"] | None = Field(
        default=None, description="本次规划用的质量档位；overseas=Grok，偏海外题材；不填则用项目保存的默认档位")


@api.post("/projects/{project_id}/plan", status_code=201)
async def plan_series(project_id: uuid.UUID, body: PlanRequest, session: Session) -> dict[str, Any]:
    """Source text -> locked series bible + look-dev cards + episode outlines.
    Short sources: one plan call. Long novels (up to 2M chars): per-segment digests (cheap summarize model,
    cached by segment sha256 in series_bible_json._cache) -> plan the bible -> outlines <=20 episodes a call.
    Progress is written to series_bible_json._progress; poll GET /projects/{id}/plan/progress."""
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    if not (p.source_novel_text or "").strip():
        raise HTTPException(422, "import a novel, an idea or a remix video first")
    try:
        segments = series.split_source(p.source_novel_text or "")
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    existing = (await session.execute(select(Episode).where(Episode.project_id == project_id))).scalars().all()
    if existing and not body.replace:
        raise HTTPException(409, "episodes already planned; pass replace=true to re-plan")
    _need_llm(p)
    mode, tier = p.content_mode, _tier(p, "plan", body.tier)
    cache: dict[str, Any] = dict((p.series_bible_json or {}).get("_cache") or {})
    await session.close()

    async def progress(state: dict[str, Any]) -> None:
        await _save_bookkeeping(project_id, {"_progress": state, "_cache": cache})

    async with _ledgered(project_id) as llm:
        try:
            if series.needs_two_stage(segments):
                plan = await _llm(series.two_stage_plan(llm, segments, body.episode_count, body.direction, mode,
                                                        cache, progress, tier=tier))
            else:
                await progress({"stage": "plan", "done": 0, "total": 1})
                plan = await _llm(llm.structured(
                    SeriesPlan, series.PLAN_RULES, {},
                    series.plan_prompt(segments, body.episode_count, body.direction), mode, effort="high",
                    stage="plan", tier=tier))
        except HTTPException as e:
            await progress({"stage": "error", "error": str(e.detail)[:500]})
            raise
        except Exception as e:
            await progress({"stage": "error", "error": str(e)[:500]})
            raise
    done = {"stage": "done", "done": len(plan.episodes), "total": len(plan.episodes),
            "episodes": len(plan.episodes)}
    async with session.begin():
        p = await session.get(Project, project_id, with_for_update=True)
        assert p is not None
        p.series_bible_json = series.bible_from_plan(plan, {"_cache": cache, "_progress": done})
        p.status = "planned"
        await session.execute(delete(Episode).where(Episode.project_id == project_id))
        session.add_all([Episode(project_id=project_id, episode_number=o.episode_number, title=o.title,
                                 synopsis=o.synopsis, source_segments=[i for i in o.source_segments
                                                                       if 0 < i <= len(segments)])
                         for o in plan.episodes])
        have = set((await session.execute(select(LookdevAsset.name).where(
            LookdevAsset.project_id == project_id))).scalars())
        cards = [("character", c.name, c.role_tag, c.visual_prompt) for c in plan.characters] + \
            [("prop", x.name, None, x.visual_prompt) for x in plan.props] + \
            [("scene", x.name, None, x.visual_prompt) for x in plan.scenes]
        session.add_all([LookdevAsset(project_id=project_id, asset_type=t, name=n, role_tag=r, visual_prompt=v,
                                      meta={"likeness_consent": False, "from_plan": True})
                         for t, n, r, v in cards if n not in have])
    return {"title": plan.title, "logline": plan.logline, "episodes": len(plan.episodes),
            "characters": len(plan.characters), "props": len(plan.props), "scenes": len(plan.scenes)}


async def _save_bookkeeping(project_id: uuid.UUID, keys: dict[str, Any]) -> None:
    """Merge "_" keys into series_bible_json in its own short transaction (the request session is closed
    during LLM calls). A new dict is assigned so the JSONB change is always flushed."""
    from src.db import SessionLocal
    async with SessionLocal() as s, s.begin():
        p = await s.get(Project, project_id, with_for_update=True)
        if p is not None:
            p.series_bible_json = {**(p.series_bible_json or {}), **keys}


@api.get("/projects/{project_id}/plan/progress")
async def plan_progress(project_id: uuid.UUID, session: Session) -> dict[str, Any]:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    bible = p.series_bible_json or {}
    return {"stage": "idle", **(bible.get("_progress") or {}), "cached_segments": len(bible.get("_cache") or {})}


@api.get("/projects/{project_id}/episodes")
async def list_episodes(project_id: uuid.UUID, session: Session) -> list[dict[str, Any]]:
    return [episode_view(e) for e in (await session.execute(select(Episode).where(
        Episode.project_id == project_id).order_by(Episode.episode_number))).scalars()]


class ImportScriptRequest(BaseModel):
    text: str = Field(min_length=1)
    mode: Literal["as_is", "ai_format", "force_as_is"] = "as_is"
    replace: bool = False


@api.post("/projects/{project_id}/episodes/import", status_code=201)
async def import_script(project_id: uuid.UUID, body: ImportScriptRequest, session: Session) -> dict[str, Any]:
    """Already-written script -> episodes directly, skipping the series-bible planning call.
    as_is/force_as_is: split on '第N集' markers (force: never split, the whole text is episode 1) and keep
    the text verbatim. ai_format: same split, then Claude reformats each segment into shooting-script form
    without changing plot or dialogue."""
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    existing = (await session.execute(select(Episode.id).where(Episode.project_id == project_id))).scalars().all()
    if existing and not body.replace:
        raise HTTPException(409, "episodes already exist; pass replace=true to overwrite")
    segments = [body.text.strip()] if body.mode == "force_as_is" else series.split_episodes(body.text)
    if not segments:
        raise HTTPException(422, "text is empty")
    if body.mode == "ai_format":
        _need_llm(p)
        mode = p.content_mode
        await session.close()
        reformatted: list[str] = []
        async with _ledgered(project_id) as llm:
            for seg in segments:
                out: EpisodeScript = await _llm(llm.structured(
                    EpisodeScript, series.REFORMAT_RULES, {}, seg, mode, stage="reformat"))
                reformatted.append(out.script_text)
        segments = reformatted
    else:
        await session.close()
    async with session.begin():
        p = await session.get(Project, project_id, with_for_update=True)
        assert p is not None
        p.source_novel_text, p.status = body.text, "scripted"
        await session.execute(delete(Episode).where(Episode.project_id == project_id))
        session.add_all([Episode(project_id=project_id, episode_number=i, title=series.episode_title(seg, i),
                                 synopsis="", script_text=seg, status="scripted")
                         for i, seg in enumerate(segments, 1)])
    return {"episodes": len(segments)}


async def _episode(session: AsyncSession, project_id: uuid.UUID, n: int) -> Episode:
    e = (await session.execute(select(Episode).where(Episode.project_id == project_id,
                                                     Episode.episode_number == n))).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, f"episode {n} not found")
    return e


class EpisodePatch(BaseModel):
    title: str | None = None
    synopsis: str | None = None
    script_text: str | None = None


@api.patch("/projects/{project_id}/episodes/{n}")
async def patch_episode(project_id: uuid.UUID, n: int, body: EpisodePatch, session: Session) -> dict[str, Any]:
    e = await _episode(session, project_id, n)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(e, k, v)
    await session.commit()
    await session.refresh(e)
    return episode_view(e)


class StageCallOptions(BaseModel):
    tier: Literal["draft", "standard", "high", "overseas"] | None = Field(
        default=None, description="本次调用用的质量档位；overseas=Grok，偏海外题材；不填则用项目保存的默认档位")


@api.post("/projects/{project_id}/episodes/{n}/script", status_code=201)
async def write_script(project_id: uuid.UUID, n: int, session: Session,
                       body: Annotated[StageCallOptions | None, Body()] = None) -> dict[str, Any]:
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    e = await _episode(session, project_id, n)
    prev = (await session.execute(select(Episode.synopsis).where(
        Episode.project_id == project_id, Episode.episode_number == n - 1))).scalar_one_or_none()
    _need_llm(p)
    segments = series.split_source(p.source_novel_text or "")
    prompt = series.script_prompt(episode_view(e), segments, prev)
    mode, bible, tier = p.content_mode, p.series_bible_json, _tier(p, "script", body.tier if body else None)
    await session.close()
    async with _ledgered(project_id) as llm:
        out: EpisodeScript = await _llm(llm.structured(EpisodeScript, series.SCRIPT_RULES, bible, prompt, mode,
                                                       stage="script", tier=tier))
    async with session.begin():
        e = await _episode(session, project_id, n)
        e.script_text, e.status = out.script_text, "scripted"
        e.title = e.title or out.episode_title
    return episode_view(e)


@api.post("/projects/{project_id}/episodes/{n}/storyboard", status_code=201)
async def storyboard_episode(project_id: uuid.UUID, n: int, session: Session,
                             body: Annotated[StageCallOptions | None, Body()] = None) -> dict[str, Any]:
    """Screenplay (or the outline if no screenplay yet) -> shots bound to the look-dev references."""
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    e = await _episode(session, project_id, n)
    _need_llm(p)
    text = e.script_text or f"第{n}集《{e.title}》大纲：{e.synopsis}"
    mode, bible = p.content_mode, p.series_bible_json
    tier = _tier(p, "storyboard", body.tier if body else None)
    await _replaceable_shots(session, project_id, n)
    await session.close()
    async with _ledgered(project_id) as llm:
        board = await _llm(llm.compile_episode_storyboard(DIRECTOR_RULES, bible, text, content_mode=mode,
                                                           tier=tier))
    shots = await orch.import_storyboard(session, project_id, n, board)
    async with session.begin():
        (await _episode(session, project_id, n)).status = "storyboarded"
    return {"created": len(shots), "episode_title": board.episode_title, "hook_summary": board.hook_summary}
