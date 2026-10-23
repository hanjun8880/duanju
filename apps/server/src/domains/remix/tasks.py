"""Remix background work, kept inside domains/remix (src/queue.py is not touched).

State per source project lives in <storage>/remix/<project_id>/manifest.json (flock-guarded), so no new table:
{"episodes": {"1": {video_path, sha256, status, progress, shots, kept, cache_hit, error, ...}}}
status: registered -> queued -> deconstructing -> deconstructed -> describing -> described | failed

Celery: the task is registered on the shared app under "remix.episode" (local_post queue). A worker must import
this module: celery -A src.queue worker -Q local_post -I src.domains.remix.tasks
"""
import asyncio
import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.config import settings
from src.domains.gateway.routing import model_for
from src.domains.remix import dedupe
from src.domains.script import series
from src.domains.script.script_engine import CachedDirectorLLM, KeyframeNotes
from src.queue import celery_app


def make_llm(model: str) -> CachedDirectorLLM:
    return CachedDirectorLLM(model=model)


def cache() -> dedupe.DeconstructCache:
    return dedupe.DeconstructCache(Path(settings.storage_dir) / "remix_cache")


def _dir(project_id: str) -> Path:
    d = Path(settings.storage_dir) / "remix" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def manifest(project_id: str) -> Iterator[dict[str, Any]]:
    """Read-modify-write under an exclusive lock; the dict is saved on exit."""
    d = _dir(project_id)
    with open(d / "manifest.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        f = d / "manifest.json"
        data = json.loads(f.read_text()) if f.is_file() else {"episodes": {}}
        yield data
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, f)


def read_manifest(project_id: str) -> dict[str, Any]:
    with manifest(project_id) as m:
        return m


def update_episode(project_id: str, ep: int, **fields: Any) -> dict[str, Any]:
    with manifest(project_id) as m:
        entry = m["episodes"].setdefault(str(ep), {})
        entry.update(fields)
        return dict(entry)


def _deconstruct(video_path: str, out_dir: str) -> list[dict[str, Any]]:
    from src.domains.script.video_remix import LocalVideoDeconstructor  # heavy: only loaded on a cache miss
    return LocalVideoDeconstructor(whisper_size=settings.whisper_size).deconstruct_for_remix(video_path, out_dir)


async def deconstruct_episode(project_id: str, ep: int) -> dict[str, Any]:
    entry = read_manifest(project_id)["episodes"][str(ep)]
    update_episode(project_id, ep, status="deconstructing", progress=0.1, error=None)
    sha, shots, hit = await asyncio.to_thread(dedupe.deconstruct_cached, entry["video_path"], cache(), _deconstruct)
    update_episode(project_id, ep, progress=0.5)
    kept, alias = await asyncio.to_thread(dedupe.dedupe_adjacent, shots)
    return update_episode(project_id, ep, status="deconstructed", progress=1.0, sha256=sha, cache_hit=hit,
                          shots=len(shots), kept=len(kept), kept_orders=[k["shot_order"] for k in kept],
                          alias={str(k): v for k, v in alias.items()})


def episode_shots(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return cache().get(entry["sha256"]) or []


async def describe_episode(project_id: str, ep: int) -> dict[str, Any]:
    """One vision pass over the deduped keyframes, cached per (sha256, model); skipped shots reuse notes."""
    entry = read_manifest(project_id)["episodes"][str(ep)]
    model = model_for("remix_vision").model
    c = cache()
    if c.get_notes(entry["sha256"], model) is None:
        update_episode(project_id, ep, status="describing", progress=0.6)
        keep = set(entry["kept_orders"])
        kept = [s for s in episode_shots(entry) if s["shot_order"] in keep]
        llm, notes = make_llm(model), dict[int, str]()
        for i in range(0, len(kept), dedupe.BATCH):
            batch = kept[i:i + dedupe.BATCH]
            res = await llm.structured(KeyframeNotes, series.KEYFRAME_RULES, {}, series.keyframe_content(batch),
                                       effort="low", max_tokens=16000, model=model)
            notes |= {n.shot_order: n.description for n in res.shots}
            update_episode(project_id, ep, progress=round(0.6 + 0.4 * (i + len(batch)) / len(kept), 2))
        c.put_notes(entry["sha256"], model, notes)
    return update_episode(project_id, ep, status="described", progress=1.0, vision_model=model)


async def run_episode(project_id: str, ep: int, describe: bool) -> str:
    try:
        if read_manifest(project_id)["episodes"][str(ep)].get("kept_orders") is None:
            await deconstruct_episode(project_id, ep)
        if describe:
            await describe_episode(project_id, ep)
        else:
            sha = read_manifest(project_id)["episodes"][str(ep)]["sha256"]
            done = cache().get_notes(sha, model_for("remix_vision").model) is not None
            update_episode(project_id, ep, status="described" if done else "deconstructed", progress=1.0)
    except Exception as e:  # noqa: BLE001 - the UI must stop waiting and show why
        update_episode(project_id, ep, status="failed", error=f"{type(e).__name__}: {e}")
        return "failed"
    return "success"


def episode_notes(entry: dict[str, Any]) -> dict[int, str]:
    model = entry.get("vision_model") or model_for("remix_vision").model
    notes = cache().get_notes(entry["sha256"], model) or {}
    return dedupe.expand_notes(notes, {int(k): v for k, v in entry.get("alias", {}).items()})


@celery_app.task(name="remix.episode")
def episode_task(project_id: str, ep: int, describe: bool) -> str:
    return asyncio.run(run_episode(project_id, ep, describe))
