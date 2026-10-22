from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/duanju"
    redis_url: str = "redis://localhost:6379/0"
    dispatch_mode: str = "celery"  # celery | inline (in-process background task; dev/tests)
    storage_dir: str = "./storage"  # local media root served at /media (MinIO/S3 not wired yet)
    default_username: str = "director"
    whisper_size: str = "large-v3"


settings = Settings()
