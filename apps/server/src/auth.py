"""Single-operator auth: one shared APP_TOKEN.

- API: `Authorization: Bearer <APP_TOKEN>` on every request except /healthz and POST /api/v1/auth/login.
- /media: the bearer, a path-bound signed URL (?exp=&sig=, see sign_media_url), or the HttpOnly cookie
  set by the login (so <img>/<video>/download links work without headers).
APP_TOKEN empty -> gate off (dev/tests); the prod compose service refuses to start without it.
"""
import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.config import settings

log = logging.getLogger(__name__)
MEDIA_COOKIE = "duanju_media"
OPEN_PATHS = {"/healthz", "/api/v1/auth/login"}
api = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _mac(msg: str) -> str:
    return hmac.new(settings.app_token.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _bearer_ok(header: str | None) -> bool:
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].encode(), settings.app_token.encode())


def sign_media_url(url: str, ttl_seconds: int = 3600) -> str:
    """'/media/x.mp4' -> '/media/x.mp4?exp=..&sig=..', valid for that path only until exp."""
    path = url.split("?", 1)[0]
    exp = int(time.time()) + ttl_seconds
    return f"{path}?exp={exp}&sig={_mac(f'{path}:{exp}')}"


def _signed_ok(path: str, exp: str | None, sig: str | None) -> bool:
    if not exp or not sig or not exp.lstrip("-").isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(sig, _mac(f"{path}:{exp}"))


def media_ticket(ttl_seconds: int | None = None) -> str:
    exp = int(time.time()) + (settings.media_ticket_ttl if ttl_seconds is None else ttl_seconds)
    return f"{exp}.{_mac(f'media-ticket:{exp}')}"


def valid_ticket(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    exp, sig = value.split(".", 1)
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(sig, _mac(f"media-ticket:{exp}"))


def _media_ok(request: Request) -> bool:
    raw_path = quote(request.url.path)
    q = request.query_params
    return (_bearer_ok(request.headers.get("authorization"))
            or _signed_ok(request.url.path, q.get("exp"), q.get("sig"))
            or _signed_ok(raw_path, q.get("exp"), q.get("sig"))
            or valid_ticket(request.cookies.get(MEDIA_COOKIE)))


async def middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if not settings.app_token or request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
        return await call_next(request)
    if request.url.path.startswith("/media/"):
        ok = _media_ok(request)
    else:
        ok = _bearer_ok(request.headers.get("authorization"))
    if not ok:
        return JSONResponse({"detail": "unauthorized"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


class Login(BaseModel):
    token: str


@api.post("/login")
async def login(body: Login, response: Response) -> dict[str, int | bool]:
    """Checks the token and sets the /media cookie. The frontend keeps the token and sends it as a bearer."""
    if settings.app_token and not hmac.compare_digest(body.token.encode(), settings.app_token.encode()):
        raise HTTPException(401, "wrong token")
    if settings.app_token:
        ticket = media_ticket()
        response.set_cookie(MEDIA_COOKIE, ticket, max_age=settings.media_ticket_ttl, httponly=True,
                            samesite="strict", path="/media")
        return {"ok": True, "expires_at": int(ticket.split(".")[0]), "auth_enabled": True}
    return {"ok": True, "expires_at": 0, "auth_enabled": False}
