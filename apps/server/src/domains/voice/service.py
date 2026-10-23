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


async def speak(client: httpx.AsyncClient, text: str, v: Voice) -> Spoken:
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
    dur = await asyncio.to_thread(_duration, hit)
    return Spoken(media.url_for(hit), str(hit), dur, cached, v.provider, v.fallback_from, key)
