"""post.assemble wiring: each shot's line is voiced with its speaker's voice (local espeak here: no vendor
keys), laid on the episode's dialogue track; animatic clips keep their own burned subtitle."""
import asyncio
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src import media, queue
from src.config import settings
from src.domains.storyboard import post
from src.domains.storyboard import shot_orchestrator as orch
from src.main import app
from src.models import Asset, LookdevAsset, Shot
from tests.test_production import BOARD, local_job, small_frames, still  # noqa: F401


async def _episode_with_animatics(sessionmaker, uid, pid):
    async with sessionmaker() as s, s.begin():
        s.add(LookdevAsset(project_id=pid, asset_type="character", name="林晚", visual_prompt="v",
                           ref_urls=[still("0x3b2f2a")], voice={"provider": "local", "voice_id": "cmn"}))
    async with sessionmaker() as s:
        shots = await orch.import_storyboard(s, pid, 1, BOARD)
    for sh in shots:
        job = await local_job(sessionmaker, uid, pid, "local_animatic", "post.animatic",
                              {"shot_id": str(sh.id), "render_tier": "L0_animatic"}, sh.id)
        assert await asyncio.to_thread(queue.animatic, str(job.id)) == "success"
    return shots


async def test_episode_gets_tts_dialogue_track(engine, sessionmaker, user_and_project, small_frames,  # noqa: F811
                                               monkeypatch):
    for k in ("VOLC_TTS_TOKEN", "VOLC_TTS_APPID", "MINIMAX_API_KEY", "PROVIDER_TTS"):
        monkeypatch.delenv(k, raising=False)
    uid, pid = user_and_project
    await _episode_with_animatics(sessionmaker, uid, pid)
    assert settings.video_native_audio is False
    job = await local_job(sessionmaker, uid, pid, "local_assemble", "post.assemble", {"episode_number": 1})
    assert await asyncio.to_thread(queue.assemble, str(job.id)) == "success"
    async with sessionmaker() as s:
        a = (await s.execute(select(Asset).where(Asset.job_id == job.id))).scalar_one()
        speakers = (await s.execute(select(Shot.speaker_asset_id).where(Shot.project_id == pid))).scalars().all()
    assert a.meta["audio_mode"] == "tts" and a.meta["dialogue_lines"] == 2
    f = orch.clip_file(a.s3_key)
    assert f is not None and post.has_audio(str(f)) and abs(post.probe_duration(str(f)) - 9.0) < 0.5
    tts = list((media.root() / "tts").rglob("*.wav"))
    assert tts  # the lines were really synthesized (and cached)
    # speaker unbound -> routing default (volc) without keys -> local, and it is reported
    if not any(speakers):
        assert all(n.get("fallback_from") == "volc" for n in a.meta["tts_notes"])


async def test_assemble_api_validates_audio_options(user_and_project):
    _, pid = user_and_project
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/projects/{pid}/assemble", json={"episode_number": 1, "audio_mode": "loud"})
        assert r.status_code == 422
        r = await c.post(f"/api/v1/projects/{pid}/assemble",
                         json={"episode_number": 1, "bgm_url": f"/media/none/{uuid.uuid4().hex}.mp3"})
        assert r.status_code == 422 and "bgm_url" in r.text
