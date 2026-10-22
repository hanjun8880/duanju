from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.models import ModelCatalog

app = FastAPI(title="Director Workbench API")
Session = Annotated[AsyncSession, Depends(get_session)]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/models")
async def list_models(session: Session) -> list[dict[str, object]]:
    # Only abstract model codes reach the frontend: no provider, api_base or keys.
    rows = (await session.execute(
        select(ModelCatalog).where(ModelCatalog.is_active).order_by(ModelCatalog.model_key))).scalars()
    return [{"model_key": m.model_key, "display_name": m.display_name, "task_type": m.task_type,
             "is_nsfw": m.is_nsfw, "credits_per_unit": str(m.credits_per_unit), "max_duration": m.max_duration,
             "resolution_multipliers": m.resolution_multipliers} for m in rows]
