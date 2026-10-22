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


async def _local_job(sm: SM, job_id: str, work: Callable[[SM, Any, Path], Awaitable[str]]) -> str:
    from src.domains.gateway import credit_ledger as ledger
    from src.models import Asset, Job
    async with sm() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
    out_dir = Path(settings.storage_dir) / "renders" / str(job.project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        out = await work(sm, job, out_dir)
    except Exception as e:  # noqa: BLE001 - any local failure must give the (pre-deducted) credits back
        async with sm() as s:
            await ledger.refund_job(s, job.id, reason="cancel_refund", error=f"{type(e).__name__}: {e}")
        return "failed"
    async with sm() as s:
        await ledger.settle_job(s, job.id, Decimal(0), extra=[Asset(job_id=job.id, project_id=job.project_id,
                                                                     kind="video", s3_key=out, meta={"local": True})])
    return "success"


@celery_app.task(name="post.animatic")
def animatic(job_id: str) -> str:
    from src.domains.storyboard import post
    from src.domains.storyboard import shot_orchestrator as orch

    async def work(sm: SM, job: Any, out_dir: Path) -> str:
        async with sm() as s:
            frames = await orch.animatic_frames(s, uuid.UUID(job.request_payload["shot_id"]))
        return await asyncio.to_thread(post.render_animatic, frames, str(out_dir / f"{job.id}_animatic.mp4"))
    return str(_run(lambda sm: _local_job(sm, job_id, work)))


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
def remix(project_id: str, video_path: str, episode_number: int) -> int:
    from src.domains.script.video_remix import LocalVideoDeconstructor
    from src.models import Shot

    out_dir = Path(settings.storage_dir) / "remix" / project_id
    shots = LocalVideoDeconstructor(whisper_size=settings.whisper_size).deconstruct_for_remix(video_path, str(out_dir))

    async def save(sm: SM) -> int:
        async with sm() as s, s.begin():
            s.add_all([Shot(project_id=uuid.UUID(project_id), episode_number=episode_number,
                            shot_order=x["shot_order"], duration=x["duration"], shot_prompt="(二创逆向待补全)",
                            dialogue_transcript=x["dialogue"] or None,
                            source_remix_meta={"source": video_path, "start_time": x["start_time"],
                                               "end_time": x["end_time"], "keyframe_path": x["keyframe_path"]})
                       for x in shots])
        return len(shots)
    return int(_run(save))
