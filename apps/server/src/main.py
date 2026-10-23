import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.db import SessionLocal
from src.deps import get_http_client
from src.domains.gateway.api import api as gateway_api
from src.domains.gateway.router import resume_polls
from src.domains.lookdev.api import api as lookdev_api
from src.domains.remix.api import api as remix_api
from src.domains.script.api import api as script_api
from src.domains.storyboard.api import api as storyboard_api

__all__ = ["app", "get_http_client"]

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # shortcut: only inline mode resumes polling after a restart; in celery mode re-queue stuck jobs via
    # /admin/jobs/{id}/retry until a beat task is added.
    task = asyncio.create_task(resume_polls(SessionLocal, get_http_client())) \
        if settings.dispatch_mode == "inline" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Director Workbench API", lifespan=lifespan)
for r in (gateway_api, script_api, lookdev_api, storyboard_api, remix_api):
    app.include_router(r)
Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
