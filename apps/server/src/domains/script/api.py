import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.config import settings
from src.deps import Session, director
from src.domains.script.script_engine import CachedDirectorLLM, EpisodeStoryboardOutput, LLMRefused
from src.domains.storyboard import shot_orchestrator as orch
from src.models import Project

api = APIRouter(prefix="/api/v1")

DIRECTOR_RULES = (
    "你是连续剧分镜导演。按短剧节奏拆镜：前3秒强冲突钩子、中段情绪递进、结尾悬念卡点。"
    "同场次连续动作优先合并为15-30秒单镜到底。five_part_visual_prompt 必须依次写：1起始构图 2摄影机运动 "
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
    return {"id": str(p.id), "title": p.title, "category": p.category, "content_mode": p.content_mode,
            "aspect_ratio": p.aspect_ratio, "status": p.status, "series_bible_json": p.series_bible_json,
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
    return project_view(p)


class StoryboardImport(BaseModel):
    episode_number: int = Field(default=1, ge=1)
    storyboard: EpisodeStoryboardOutput


@api.post("/projects/{project_id}/storyboard", status_code=201)
async def import_storyboard(project_id: uuid.UUID, body: StoryboardImport, session: Session) -> dict[str, Any]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
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
    if p.content_mode == "standard_sfw" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured on the server")
    mode, bible = p.content_mode, p.series_bible_json
    await session.close()
    try:
        board = await CachedDirectorLLM().compile_episode_storyboard(DIRECTOR_RULES, bible, body.text,
                                                                     content_mode=mode)
    except LLMRefused as e:
        raise HTTPException(422, f"model declined: {e}") from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    shots = await orch.import_storyboard(session, project_id, body.episode_number, board)
    return {"created": len(shots), "episode_title": board.episode_title, "hook_summary": board.hook_summary}


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


class RemixRequest(BaseModel):
    video_path: str
    episode_number: int = Field(default=1, ge=1)


@api.post("/projects/{project_id}/remix", status_code=202)
async def remix(project_id: uuid.UUID, body: RemixRequest, session: Session) -> dict[str, str]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    root = Path(settings.storage_dir).resolve()
    src = Path(body.video_path).resolve()
    if root not in src.parents or not src.is_file():  # only files we stored ourselves
        raise HTTPException(422, "video_path must be a file returned by /uploads")
    orch.enqueue_local("remix.deconstruct", str(project_id), str(src), body.episode_number)
    return {"queue": "local_post", "status": "queued"}
