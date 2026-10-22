from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/duanju"
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
