from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/duanju"
    redis_url: str = "redis://localhost:6379/0"
    dispatch_mode: str = "celery"  # celery | inline (in-process background task; dev/tests)
    storage_dir: str = "./storage"  # local media root served at /media (MinIO/S3 not wired yet)
    default_username: str = "director"
    whisper_size: str = "large-v3"
    # Vendor video clips carry their own (per-shot, inconsistent) voices only when this is on. Off: renders ask
    # for silent clips and the episode dialogue track is character TTS ("tts"); on: default track is "native".
    video_native_audio: bool = False
    app_token: str = ""  # Bearer token for every API call; empty = auth off (local dev / tests only)
    media_ticket_ttl: int = 12 * 3600  # seconds a login's /media cookie stays valid


settings = Settings()
