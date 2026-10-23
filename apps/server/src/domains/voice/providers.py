"""TTS adapters: Volcengine (火山) V1 HTTP, MiniMax t2a_v2, and local espeak-ng (silence if espeak is absent).

Endpoints / field names come from the vendors' public docs as summarized 2026-10 and were NOT exercised with a
real key: every value marked UNVERIFIED lives in CONFIG and can be overridden by env without code changes.
"""
import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

CONFIG: dict[str, dict[str, Any]] = {
    "volc": {
        "url": os.environ.get("VOLC_TTS_URL", "https://openspeech.bytedance.com/api/v1/tts"),
        "cluster": os.environ.get("VOLC_TTS_CLUSTER", "volcano_tts"),
        "encoding": "mp3",  # UNVERIFIED: wav/pcm/ogg_opus also documented
        "success_code": 3000,  # UNVERIFIED: V1 returns code 3000 on success
        "default_voice": os.environ.get("VOLC_TTS_DEFAULT_VOICE", "BV700_streaming"),  # UNVERIFIED voice id
    },
    "minimax": {
        "url": os.environ.get("MINIMAX_TTS_URL", "https://api.minimax.io/v1/t2a_v2"),
        "model": os.environ.get("MINIMAX_TTS_MODEL", "speech-02-hd"),  # UNVERIFIED model id
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},  # UNVERIFIED
        "default_voice": os.environ.get("MINIMAX_TTS_DEFAULT_VOICE", "female-shaonv"),  # UNVERIFIED voice id
    },
    "local": {"default_voice": os.environ.get("ESPEAK_VOICE", "cmn"), "wpm": 175,
              "silence_chars_per_s": 4.0},
}
CREDENTIAL_ENV = {"volc": ("VOLC_TTS_APPID", "VOLC_TTS_TOKEN"), "minimax": ("MINIMAX_API_KEY",), "local": ()}


class TTSError(RuntimeError):
    pass


@dataclass(frozen=True)
class Voice:
    provider: str  # volc | minimax | local
    voice_id: str = ""
    speed: float = 1.0
    emotion: str | None = None
    vol: float = 1.0
    pitch: int = 0
    fallback_from: str | None = field(default=None, compare=False)

    def cache_fields(self) -> dict[str, Any]:
        return {"provider": self.provider, "voice_id": self.voice_id or CONFIG[self.provider]["default_voice"],
                "speed": self.speed, "emotion": self.emotion, "vol": self.vol, "pitch": self.pitch,
                "model": CONFIG[self.provider].get("model", "")}


@dataclass(frozen=True)
class Audio:
    data: bytes
    ext: str


def configured(provider: str) -> bool:
    return all(os.environ.get(k) for k in CREDENTIAL_ENV.get(provider, ("_missing_",)))


async def _volc(client: httpx.AsyncClient, text: str, v: Voice) -> Audio:
    cfg, token = CONFIG["volc"], os.environ["VOLC_TTS_TOKEN"]
    audio: dict[str, Any] = {"voice_type": v.voice_id or cfg["default_voice"], "encoding": cfg["encoding"],
                             "speed_ratio": v.speed, "volume_ratio": v.vol}
    if v.emotion:
        audio["emotion"] = v.emotion
    body = {"app": {"appid": os.environ["VOLC_TTS_APPID"], "token": token, "cluster": cfg["cluster"]},
            "user": {"uid": "duanju"}, "audio": audio,
            "request": {"reqid": uuid.uuid4().hex, "text": text, "operation": "query"}}
    r = await client.post(cfg["url"], json=body, headers={"Authorization": f"Bearer;{token}"}, timeout=60)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or data.get("code") != cfg["success_code"] or not data.get("data"):
        raise TTSError(f"volc tts {r.status_code} code={data.get('code')} {data.get('message', r.text[:200])}")
    return Audio(base64.b64decode(data["data"]), "." + cfg["encoding"])


async def _minimax(client: httpx.AsyncClient, text: str, v: Voice) -> Audio:
    cfg = CONFIG["minimax"]
    vs: dict[str, Any] = {"voice_id": v.voice_id or cfg["default_voice"], "speed": v.speed, "vol": v.vol,
                          "pitch": v.pitch}
    if v.emotion:
        vs["emotion"] = v.emotion
    body = {"model": cfg["model"], "text": text, "stream": False, "output_format": "hex", "voice_setting": vs,
            "audio_setting": cfg["audio_setting"]}
    r = await client.post(cfg["url"], json=body, timeout=60,
                          headers={"Authorization": f"Bearer {os.environ['MINIMAX_API_KEY']}"})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    base = data.get("base_resp") or {}
    audio_hex = (data.get("data") or {}).get("audio")
    if r.status_code != 200 or base.get("status_code") != 0 or not audio_hex:
        raise TTSError(f"minimax tts {r.status_code} status_code={base.get('status_code')} "
                       f"{base.get('status_msg', r.text[:200])}")
    return Audio(bytes.fromhex(audio_hex), "." + cfg["audio_setting"]["format"])


def silence_wav(seconds: float, rate: int = 16000) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x00\x00" * int(seconds * rate))
        return Path(f.name).read_bytes()


def _local_sync(text: str, v: Voice) -> Audio:
    cfg = CONFIG["local"]
    binary = shutil.which("espeak-ng")
    if binary is None:  # keeps timing usable (subtitle/assembly) without any TTS installed
        return Audio(silence_wav(max(len(text) / cfg["silence_chars_per_s"], 0.5)), ".wav")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "o.wav"
        subprocess.run([binary, "-v", v.voice_id or cfg["default_voice"], "-s", str(int(cfg["wpm"] * v.speed)),
                        "-w", str(out), text], check=True, capture_output=True, timeout=60)
        return Audio(out.read_bytes(), ".wav")


async def synthesize(client: httpx.AsyncClient, text: str, v: Voice) -> Audio:
    if v.provider == "volc":
        return await _volc(client, text, v)
    if v.provider == "minimax":
        return await _minimax(client, text, v)
    if v.provider == "local":
        return await asyncio.to_thread(_local_sync, text, v)
    raise TTSError(f"unknown tts provider {v.provider}")
