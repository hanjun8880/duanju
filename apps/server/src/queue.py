"""Three physically separated Celery queue tiers on Redis (spec layer ③).

high_priority   seconds-long cloud T2I / face-swap calls
video.<model>   one queue per video model so a slow vendor never blocks another
local_post      zero-credit local work: L0 animatic, InsightFace, Real-ESRGAN, OTIO assembly, remix deconstruct
Run e.g.: celery -A src.queue worker -Q video.seedance_2_5_spicy -c 2   /   -Q local_post -c 1 --pool solo
"""
import asyncio
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.models import Job

QUEUE_HIGH, QUEUE_LOCAL = "high_priority", "local_post"
celery_app = Celery("director", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_default_queue=QUEUE_HIGH, task_acks_late=True, worker_prefetch_multiplier=1,
                       broker_connection_retry_on_startup=True)


SM = async_sessionmaker[AsyncSession]


def queue_for(task_type: str, model_key: str, provider: str) -> str:
    if provider == "local":
        return QUEUE_LOCAL
    if task_type.startswith("video."):
        return f"video.{model_key}"
    return QUEUE_HIGH


def _run(fn: Callable[[SM], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        engine = create_async_engine(settings.database_url)  # per task: asyncpg pools are loop-bound
        try:
            return await fn(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    return asyncio.run(main())


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="gateway.dispatch")
def dispatch(job_id: str) -> str:
    from src.domains.gateway import router

    async def go(sm: SM) -> str:
        async with httpx.AsyncClient() as client:
            job = await router.dispatch_job(sm, uuid.UUID(job_id), client)
        return job.status
    return str(_run(go))


async def _local_job(sm: SM, job_id: str, work: Callable[[SM, Any, Path], Awaitable[str]], kind: str = "video",
                     meta: dict[str, Any] | None = None) -> str:
    from src.domains.gateway import credit_ledger as ledger
    from src.models import Asset
    async with sm() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
    out_dir = Path(settings.storage_dir) / "renders" / str(job.project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        out = await work(sm, job, out_dir)
        async with sm() as s:
            await ledger.settle_job(s, job.id, Decimal(0), extra=[Asset(
                job_id=job.id, project_id=job.project_id, kind=kind, s3_key=out, meta={"local": True, **(meta or {})})])
    except Exception as e:  # noqa: BLE001 - any local failure must give the (pre-deducted) credits back
        async with sm() as s:
            await ledger.refund_job(s, job.id, reason="cancel_refund", error=f"{type(e).__name__}: {e}")
        return "failed"
    return "success"


@celery_app.task(name="post.animatic")
def animatic(job_id: str) -> str:
    from src.domains.storyboard import post
    from src.domains.storyboard import shot_orchestrator as orch

    async def work(sm: SM, job: Any, out_dir: Path) -> str:
        from src.models import Project
        async with sm() as s:
            frames = await orch.animatic_frames(s, uuid.UUID(job.request_payload["shot_id"]))
            project = await s.get(Project, job.project_id)
        size = post.SIZES.get(project.aspect_ratio if project else "9:16", (1080, 1920))
        return await asyncio.to_thread(post.render_animatic, frames, str(out_dir / f"{job.id}_animatic.mp4"), size)
    return str(_run(lambda sm: _local_job(sm, job_id, work)))


@celery_app.task(name="post.assemble")
def assemble(job_id: str) -> str:
    """Episode master (best clip per shot) or whole series (latest master per episode)."""
    from sqlalchemy import select

    from src.domains.storyboard import post
    from src.domains.storyboard import shot_orchestrator as orch
    from src.models import Asset, Project, Shot

    async def run(sm: SM) -> str:
        async with sm() as s:
            job = await s.get(Job, uuid.UUID(job_id))
            assert job is not None
            ep = job.request_payload.get("episode_number")
        meta: dict[str, Any] = {"episode_number": ep}

        async def work(sm: SM, job: Any, out_dir: Path) -> str:
            async with sm() as s:
                project = await s.get(Project, job.project_id)
                assert project is not None
                if ep is not None:
                    best = await orch.best_clips(s, job.project_id, ep)
                    order = (await s.execute(select(Shot.id).where(Shot.project_id == job.project_id,
                                                                   Shot.episode_number == ep)
                                             .order_by(Shot.shot_order))).scalars().all()
                    clips = [str(best[i]) for i in order if i in best]
                    meta["shots_total"], meta["shots_used"] = len(order), len(clips)
                else:
                    latest: dict[int, str] = {}
                    for a in (await s.execute(select(Asset).where(Asset.project_id == job.project_id,
                                                                  Asset.kind == "episode")
                                              .order_by(Asset.created_at))).scalars():
                        f = orch.clip_file(a.s3_key)
                        if f is not None:
                            latest[int(a.meta["episode_number"])] = str(f)
                    clips = [latest[k] for k in sorted(latest)]
                    meta["episodes"] = sorted(latest)
            if not clips:
                raise ValueError("nothing to assemble")
            name = f"EP{ep:02d}" if ep is not None else "SERIES"
            size = post.SIZES.get(project.aspect_ratio, (1080, 1920))
            return await asyncio.to_thread(post.assemble_episode, clips, str(out_dir / f"{name}_{job.id}.mp4"),
                                           str(out_dir / f"{name}_{job.id}.otio"), name, size)
        return await _local_job(sm, job_id, work, kind="episode" if ep is not None else "series", meta=meta)
    return str(_run(run))


@celery_app.task(name="post.upscale")
def upscale(job_id: str) -> str:
    from src.domains.storyboard import post

    async def work(sm: SM, job: Any, out_dir: Path) -> str:
        src = job.request_payload["request"]["refs"]["video_urls"][0]
        return await asyncio.to_thread(post.upscale_4k, src, str(out_dir / f"{job.id}_4k.mp4"))
    return str(_run(lambda sm: _local_job(sm, job_id, work)))


@celery_app.task(name="face.swap")
def face_swap(job_id: str) -> str:
    from src.domains.storyboard import post

    async def work(sm: SM, job: Any, out_dir: Path) -> str:
        refs = job.request_payload["request"]["refs"]
        return await asyncio.to_thread(post.face_swap, refs["video_urls"][0], refs["image_urls"][0],
                                       str(out_dir / f"{job.id}_swap.mp4"))
    return str(_run(lambda sm: _local_job(sm, job_id, work)))


@celery_app.task(name="remix.deconstruct")
def remix(project_id: str, video_path: str) -> int:
    """Local cut + transcript, then (if Claude is configured) one batched keyframe description call.
    The result becomes the project's source text, so remix follows the same plan -> script -> storyboard path."""
    import os

    from src.domains.script import series, usage
    from src.domains.script.script_engine import CachedDirectorLLM, KeyframeNotes
    from src.domains.script.video_remix import LocalVideoDeconstructor
    from src.models import Project

    out_dir = Path(settings.storage_dir) / "remix" / project_id
    try:
        shots = LocalVideoDeconstructor(whisper_size=settings.whisper_size).deconstruct_for_remix(video_path,
                                                                                                 str(out_dir))
    except Exception:
        _run(lambda sm: _mark(sm, project_id, "remix_failed"))  # the UI stops waiting and shows the failure
        raise

    async def save(sm: SM) -> int:
        async with sm() as s:
            p = await s.get(Project, uuid.UUID(project_id))
            assert p is not None
            mode = p.content_mode
        notes: dict[int, str] = {}
        if mode == "standard_sfw" and os.environ.get("ANTHROPIC_API_KEY"):
            llm = CachedDirectorLLM()
            for i in range(0, len(shots), 40):  # stay well under the per-request image limit
                batch = shots[i:i + 40]
                res = await llm.structured(KeyframeNotes, series.KEYFRAME_RULES, {}, series.keyframe_content(batch),
                                           effort="low", max_tokens=16000, stage="remix_vision")
                notes |= {n.shot_order: n.description for n in res.shots}
            await usage.record_llm_calls(project_id, llm.calls, sm)
        async with sm() as s, s.begin():
            p = await s.get(Project, uuid.UUID(project_id), with_for_update=True)
            assert p is not None
            p.source_novel_text, p.status = series.remix_source_text(shots, notes), "source_ready"
        return len(shots)
    try:
        return int(_run(save))
    except Exception:
        _run(lambda sm: _mark(sm, project_id, "remix_failed"))
        raise


async def _mark(sm: SM, project_id: str, status: str) -> None:
    from src.models import Project
    async with sm() as s, s.begin():
        p = await s.get(Project, uuid.UUID(project_id), with_for_update=True)
        if p is not None:
            p.status = status
