"""Pick a voice (character voice -> routing-table stage "tts"), strip a line to what is spoken, and synthesize
through a content-addressed cache: the same (text, voice) is paid for once."""
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src import media
from src.domains.gateway.routing import model_for
from src.domains.voice.providers import Voice, configured, synthesize

ROUTING_PROVIDER = {"volc_tts": "volc", "volc": "volc", "minimax_tts": "minimax", "minimax": "minimax",
                    "local": "local"}
_SPEAKER = re.compile(r"^\s*(?:[（(][^）)]{1,12}[）)]|[^：:\s“\"「]{1,12}\s*[：:])\s*")
_QUOTES = "“”\"「」『』‘’'"


def spoken_text(line: str | None) -> str:
    """'林夏：“你终于来了。”' -> '你终于来了。' (speaker label / (旁白) and outer quotes removed)."""
    t = _SPEAKER.sub("", (line or "").strip(), count=1).strip()
    return t.strip(_QUOTES).strip()


def resolve_voice(character_voice: dict[str, Any] | None) -> Voice:
    """Character's own voice.provider wins; otherwise the routing table's tts stage. A provider without
    credentials falls back to local espeak (fallback_from says which one was wanted)."""
    cv = dict(character_voice or {})
    provider = ROUTING_PROVIDER.get(str(cv.get("provider") or ""), "") or \
        ROUTING_PROVIDER.get(model_for("tts").provider, "local")
    if not cv.get("provider"):
        cv = {}  # a voice_id without a provider belongs to no specific vendor
    if not configured(provider):
        return Voice("local", fallback_from=provider, speed=float(cv.get("speed") or 1.0))
    return Voice(provider, voice_id=str(cv.get("voice_id") or ""), speed=float(cv.get("speed") or 1.0),
                 emotion=cv.get("emotion") or None, vol=float(cv.get("vol") or 1.0), pitch=int(cv.get("pitch") or 0))


def cache_key(text: str, v: Voice) -> str:
    return hashlib.sha256(json.dumps({"text": text, **v.cache_fields()}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Spoken:
    url: str
    path: str
    duration: float
    cached: bool
    provider: str
    fallback_from: str | None
    key: str


def _duration(p: Path) -> float:
    from src.domains.storyboard.post import probe_duration
    return probe_duration(str(p))


def tts_usd(provider: str, text: str) -> float | None:
    from src.config import settings
    price = settings.tts_usd_per_char.get(provider)
    return None if price is None else round(price * len(text), 6)


async def record_tts(project_id: Any, shot_id: Any, text: str, v: Voice, key: str,
                     sm: Any = None) -> None:
    """One zero-credit jobs row per paid (cache-miss) synthesis so TTS shows up in the cost views."""
    from sqlalchemy import func

    from src.models import Job
    if sm is None:
        from src.db import SessionLocal
        sm = SessionLocal
    async with sm() as s, s.begin():
        s.add(Job(project_id=project_id, shot_id=shot_id, task_type="tts", model_key="tts",
                  request_payload={"stage": "tts", "provider": v.provider, "fallback_from": v.fallback_from,
                                   "chars": len(text), "usd": tts_usd(v.provider, text), "cache_key": key,
                                   "price_verified": False},
                  credit_estimate=0, actual_credits=0, status="success", completed_at=func.now()))


async def speak(client: httpx.AsyncClient, text: str, v: Voice, *, project_id: Any = None, shot_id: Any = None,
                sm: Any = None) -> Spoken:
    """Cached by (text, voice). A miss with a project to bill writes a task_type='tts' ledger row."""
    key = cache_key(text, v)
    d = media.root() / "tts" / key[:2]
    hit = next(iter(sorted(d.glob(f"{key}.*"))), None) if d.is_dir() else None
    cached = hit is not None
    if hit is None:
        audio = await synthesize(client, text, v)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{key}.part"
        tmp.write_bytes(audio.data)
        hit = d / f"{key}{audio.ext}"
        tmp.replace(hit)  # atomic: a concurrent reader never sees half a file
        if project_id is not None:
            await record_tts(project_id, shot_id, text, v, key, sm)
    dur = await asyncio.to_thread(_duration, hit)
    return Spoken(media.url_for(hit), str(hit), dur, cached, v.provider, v.fallback_from, key)


def default_audio_mode() -> str:
    from src.config import settings
    return "native" if settings.video_native_audio else "tts"


async def dialogue_lines(session: Any, client: httpx.AsyncClient, shots: list[Any], clips: dict[Any, Path],
                         audio_mode: str, sm: Any = None) -> tuple[list[Any], list[dict[str, Any]]]:
    """One post.Line (or None) per shot that has a clip, in order. TTS only when the mode uses it; a failing
    vendor falls back to local espeak for that line (reported in the returned notes, not hidden)."""
    from src.domains.storyboard.post import Line
    from src.domains.voice.providers import TTSError
    from src.models import LookdevAsset
    lines: list[Any] = []
    notes: list[dict[str, Any]] = []
    for sh in shots:
        if sh.id not in clips:
            continue
        text = spoken_text(sh.dialogue_transcript)
        if not text:
            lines.append(None)
            continue
        burned = clips[sh.id].name.endswith("_animatic.mp4")  # L0 animatics already burn their subtitle
        audio = None
        if audio_mode != "native":
            speaker = await session.get(LookdevAsset, sh.speaker_asset_id) if sh.speaker_asset_id else None
            v = resolve_voice(speaker.voice if speaker else None)
            try:
                spoken = await speak(client, text, v, project_id=sh.project_id, shot_id=sh.id, sm=sm)
            except TTSError as e:
                notes.append({"shot_id": str(sh.id), "provider": v.provider, "error": str(e)[:200]})
                spoken = await speak(client, text, Voice("local", fallback_from=v.provider), project_id=sh.project_id,
                                     shot_id=sh.id, sm=sm)
            if spoken.fallback_from:
                notes.append({"shot_id": str(sh.id), "fallback_from": spoken.fallback_from})
            audio = spoken.path
        lines.append(Line(text, audio, subtitle=not burned))
    return lines, notes
