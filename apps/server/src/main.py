from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.deps import get_http_client
from src.domains.gateway.api import api as gateway_api
from src.domains.lookdev.api import api as lookdev_api
from src.domains.script.api import api as script_api
from src.domains.storyboard.api import api as storyboard_api

__all__ = ["app", "get_http_client"]

app = FastAPI(title="Director Workbench API")
for r in (gateway_api, script_api, lookdev_api, storyboard_api):
    app.include_router(r)
Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
