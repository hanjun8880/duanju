from typing import Annotated

import httpx
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import get_session
from src.models import User

Session = Annotated[AsyncSession, Depends(get_session)]
_http: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient()
    return _http


Http = Annotated[httpx.AsyncClient, Depends(get_http_client)]


async def director(session: AsyncSession) -> User:
    """Single-operator workbench: one local director account (no auth layer yet)."""
    user = (await session.execute(select(User).where(User.username == settings.default_username))).scalar_one_or_none()
    if user is None:
        user = User(username=settings.default_username)
        session.add(user)
        await session.commit()
    return user
