import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src import auth
from src.config import settings
from src.db import SessionLocal
from src.deps import get_http_client
from src.domains.gateway.api import api as gateway_api
from src.domains.gateway.router import resume_polls
from src.domains.lookdev.api import api as lookdev_api
from src.domains.script.api import api as script_api
from src.domains.storyboard.api import api as storyboard_api
from src.domains.voice.api import api as voice_api

__all__ = ["app", "get_http_client"]

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # shortcut: only inline mode resumes polling after a restart; in celery mode re-queue stuck jobs via
    # /admin/jobs/{id}/retry until a beat task is added.
    if not settings.app_token:
        logging.getLogger(__name__).warning("APP_TOKEN is empty: API and /media are unauthenticated")
    task = asyncio.create_task(resume_polls(SessionLocal, get_http_client())) \
        if settings.dispatch_mode == "inline" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Director Workbench API", lifespan=lifespan)
app.middleware("http")(auth.middleware)
for r in (auth.api, gateway_api, script_api, lookdev_api, storyboard_api, voice_api):
    app.include_router(r)
Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
