"""Character TTS: three adapters (Volc / MiniMax stubbed at the transport layer, local espeak-ng real),
provider selection (character voice -> routing table), and the (text, voice) content cache."""
import base64
import json
import subprocess
import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.domains.voice import providers as P
from src.domains.voice import service as S

FAKE_MP3: bytes = b""


@pytest.fixture(scope="module")
def mp3_bytes(tmp_path_factory) -> bytes:
    p = tmp_path_factory.mktemp("a") / "x.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1.2", "-c:a", "libmp3lame", str(p)],
                   check=True, capture_output=True)
    return p.read_bytes()


@pytest.fixture
def creds(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    monkeypatch.setenv("VOLC_TTS_APPID", "app1")
    monkeypatch.setenv("VOLC_TTS_TOKEN", "tok1")
    monkeypatch.setenv("MINIMAX_API_KEY", "mmkey")
    monkeypatch.delenv("PROVIDER_TTS", raising=False)


def volc_transport(mp3: bytes, seen: list[httpx.Request]) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"reqid": "r", "code": 3000, "message": "Success",
                                         "data": base64.b64encode(mp3).decode()})
    return httpx.MockTransport(h)


def minimax_transport(mp3: bytes, seen: list[httpx.Request], code: int = 0) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"data": {"audio": mp3.hex(), "status": 2},
                                         "base_resp": {"status_code": code, "status_msg": "ok" if not code else "bad"}})
    return httpx.MockTransport(h)


async def test_volc_request_shape(creds, mp3_bytes):
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=volc_transport(mp3_bytes, seen)) as c:
        audio = await P.synthesize(c, "你终于来了。", P.Voice("volc", "BV700_streaming", speed=1.1, emotion="sad"))
    assert audio.data == mp3_bytes and audio.ext == ".mp3"
    req = seen[0]
    assert str(req.url) == "https://openspeech.bytedance.com/api/v1/tts"
    assert req.headers["authorization"] == "Bearer;tok1"
    body = json.loads(req.content)
    assert body["app"] == {"appid": "app1", "token": "tok1", "cluster": "volcano_tts"}
    assert body["audio"]["voice_type"] == "BV700_streaming" and body["audio"]["speed_ratio"] == 1.1
    assert body["audio"]["emotion"] == "sad"
    assert body["request"]["text"] == "你终于来了。" and body["request"]["operation"] == "query"
    assert body["request"]["reqid"] and body["user"]["uid"]


async def test_volc_error_raises(creds):
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"code": 3001, "message": "invalid voice"}))
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(P.TTSError, match="3001"):
            await P.synthesize(c, "hi", P.Voice("volc", "nope"))


async def test_minimax_request_shape_and_error(creds, mp3_bytes):
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=minimax_transport(mp3_bytes, seen)) as c:
        audio = await P.synthesize(c, "台词", P.Voice("minimax", "female-shaonv", speed=0.9, emotion="happy"))
    assert audio.data == mp3_bytes
    req = seen[0]
    assert str(req.url) == "https://api.minimax.io/v1/t2a_v2"
    assert req.headers["authorization"] == "Bearer mmkey"
    body = json.loads(req.content)
    assert body["stream"] is False and body["output_format"] == "hex" and body["text"] == "台词"
    assert body["voice_setting"]["voice_id"] == "female-shaonv" and body["voice_setting"]["speed"] == 0.9
    assert body["voice_setting"]["emotion"] == "happy"
    async with httpx.AsyncClient(transport=minimax_transport(mp3_bytes, [], code=1004)) as c:
        with pytest.raises(P.TTSError, match="1004"):
            await P.synthesize(c, "台词", P.Voice("minimax", "x"))


async def test_local_espeak_is_real_audio(creds):
    async with httpx.AsyncClient() as c:
        audio = await P.synthesize(c, "你好，世界", P.Voice("local", "cmn"))
    assert audio.ext == ".wav" and audio.data[:4] == b"RIFF" and len(audio.data) > 8000


def test_resolve_order(creds, monkeypatch):
    assert S.resolve_voice({"provider": "minimax", "voice_id": "v1", "speed": 1.2}).provider == "minimax"
    # no character voice -> routing table stage "tts" (volc_tts by default)
    assert S.resolve_voice({}).provider == "volc"
    monkeypatch.setenv("PROVIDER_TTS", "minimax_tts")
    assert S.resolve_voice(None).provider == "minimax"
    monkeypatch.setenv("PROVIDER_TTS", "local")
    assert S.resolve_voice(None).provider == "local"


def test_missing_credentials_fall_back_to_local(monkeypatch, tmp_path):
    monkeypatch.delenv("VOLC_TTS_TOKEN", raising=False)
    monkeypatch.delenv("VOLC_TTS_APPID", raising=False)
    v = S.resolve_voice({"provider": "volc", "voice_id": "BV700"})
    assert v.provider == "local" and v.fallback_from == "volc"


async def test_cache_by_text_and_voice(creds, mp3_bytes):
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=volc_transport(mp3_bytes, seen)) as c:
        v = P.Voice("volc", "BV700")
        a = await S.speak(c, "同一句台词", v)
        b = await S.speak(c, "同一句台词", v)
        d = await S.speak(c, "同一句台词", P.Voice("volc", "BV701"))
    assert len(seen) == 2
    assert a.url == b.url and a.url != d.url and b.cached and not a.cached
    assert a.url.startswith("/media/tts/") and abs(a.duration - 1.2) < 0.15


def test_spoken_text_strips_speaker_and_quotes():
    assert S.spoken_text("林夏：“你终于来了。”") == "你终于来了。"
    assert S.spoken_text("Lin: hello") == "hello"
    assert S.spoken_text("（旁白）天亮了") == "天亮了"
    assert S.spoken_text("  ") == ""


async def test_voice_api_preview_and_character_voice(creds, mp3_bytes, user_and_project, monkeypatch):
    from src.deps import get_http_client
    from src.main import app
    _, pid = user_and_project
    seen: list[httpx.Request] = []
    stub = httpx.AsyncClient(transport=minimax_transport(mp3_bytes, seen))
    app.dependency_overrides[get_http_client] = lambda: stub
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/v1/projects/{pid}/lookdev", json={"asset_type": "character", "name": "林夏",
                                                                     "visual_prompt": "x"})
            aid = r.json()["id"]
            r = await c.put(f"/api/v1/voice/characters/{aid}", json={"provider": "minimax", "voice_id": "v-lin",
                                                                     "speed": 1.0})
            assert r.status_code == 200 and r.json()["voice"]["voice_id"] == "v-lin"
            r = await c.post("/api/v1/voice/preview", json={"text": "林夏：你好", "asset_id": aid})
            assert r.status_code == 200, r.text
            assert r.json()["provider"] == "minimax" and r.json()["url"].startswith("/media/tts/")
            assert json.loads(seen[0].content)["text"] == "你好"
            provs = (await c.get("/api/v1/voice/providers")).json()
            assert {p["id"] for p in provs} == {"volc", "minimax", "qwen", "local"}
            assert (await c.put(f"/api/v1/voice/characters/{uuid.uuid4()}",
                                json={"provider": "local", "voice_id": "cmn"})).status_code == 404
    finally:
        app.dependency_overrides.clear()
        await stub.aclose()
