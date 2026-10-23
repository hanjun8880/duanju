import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.deps import Http, Session
from src.domains.voice import providers
from src.domains.voice.service import resolve_voice, speak, spoken_text
from src.models import LookdevAsset, Shot

api = APIRouter(prefix="/api/v1/voice", tags=["voice"])


class VoiceSettings(BaseModel):
    provider: Literal["volc", "minimax", "local"]
    voice_id: str = Field(default="", max_length=128)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    emotion: str | None = Field(default=None, max_length=32)
    vol: float = Field(default=1.0, gt=0, le=10)
    pitch: int = Field(default=0, ge=-12, le=12)


@api.get("/providers")
async def list_providers() -> list[dict[str, Any]]:
    return [{"id": p, "configured": providers.configured(p),
             "default_voice": providers.CONFIG[p]["default_voice"]} for p in ("volc", "minimax", "local")]


@api.put("/characters/{asset_id}")
async def set_character_voice(asset_id: uuid.UUID, body: VoiceSettings, session: Session) -> dict[str, Any]:
    a = await session.get(LookdevAsset, asset_id)
    if a is None or a.asset_type != "character":
        raise HTTPException(404, "character not found")
    a.voice = body.model_dump()
    await session.commit()
    return {"id": str(a.id), "name": a.name, "voice": a.voice}


class Preview(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    asset_id: uuid.UUID | None = None
    voice: VoiceSettings | None = None


def _view(s: Any) -> dict[str, Any]:
    return {"url": s.url, "duration": round(s.duration, 3), "cached": s.cached, "provider": s.provider,
            "fallback_from": s.fallback_from}


@api.post("/preview")
async def preview(body: Preview, session: Session, client: Http) -> dict[str, Any]:
    """Speak one line with an explicit voice, or with a character's saved voice. Cached by (text, voice)."""
    cv: dict[str, Any] | None = body.voice.model_dump() if body.voice else None
    if cv is None and body.asset_id:
        a = await session.get(LookdevAsset, body.asset_id)
        if a is None:
            raise HTTPException(404, "character not found")
        cv = a.voice
    text = spoken_text(body.text)
    if not text:
        raise HTTPException(422, "nothing to speak")
    try:
        return _view(await speak(client, text, resolve_voice(cv)))
    except providers.TTSError as e:
        raise HTTPException(502, str(e)) from e


@api.post("/shots/{shot_id}")
async def voice_shot(shot_id: uuid.UUID, session: Session, client: Http) -> dict[str, Any]:
    """The shot's dialogue in its speaker's voice (what post.assemble lays on the dialogue track)."""
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(404, "shot not found")
    text = spoken_text(shot.dialogue_transcript)
    if not text:
        raise HTTPException(422, "shot has no dialogue")
    speaker = await session.get(LookdevAsset, shot.speaker_asset_id) if shot.speaker_asset_id else None
    try:
        return _view(await speak(client, text, resolve_voice(speaker.voice if speaker else None)))
    except providers.TTSError as e:
        raise HTTPException(502, str(e)) from e
