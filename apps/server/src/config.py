from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/duanju"
    redis_url: str = "redis://localhost:6379/0"
    dispatch_mode: str = "celery"  # celery | inline (in-process background task; dev/tests)
    storage_dir: str = "./storage"  # local media root served at /media (MinIO/S3 not wired yet)
    default_username: str = "director"
    whisper_size: str = "large-v3"
    # LLM USD per million tokens (placeholders, unverified; override whole table with env LLM_PRICES as JSON).
    # cache_write is the 1h-TTL write price (2x input).
    llm_prices: dict[str, dict[str, float]] = {
        "claude-opus-5-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 10.0},
        "claude-sonnet-5-5": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 6.0},
    }


settings = Settings()
