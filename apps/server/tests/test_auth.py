"""APP_TOKEN bearer gate: everything but /healthz and the login needs it; /media takes a bearer, a signed
short-lived URL, or the cookie the login sets."""
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src import auth
from src.config import settings
from src.main import app

TOKEN = "s3cret-token-for-tests"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "app_token", TOKEN)
    return TOKEN


@pytest.fixture
def media_file():
    p = Path(settings.storage_dir) / "authtest" / "a.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("hello")
    return "/media/authtest/a.txt"


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_api_requires_bearer(token):
    async with client() as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/api/v1/models")).status_code == 401
        assert (await c.get("/api/v1/models", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/api/v1/models", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
        assert (await c.get("/openapi.json")).status_code == 401


async def test_no_token_configured_means_open(monkeypatch):
    monkeypatch.setattr(settings, "app_token", "")
    async with client() as c:
        assert (await c.get("/api/v1/models")).status_code == 200


async def test_login_checks_token_and_sets_media_cookie(token, media_file):
    async with client() as c:
        assert (await c.post("/api/v1/auth/login", json={"token": "nope"})).status_code == 401
        assert (await c.get(media_file)).status_code == 401
        r = await c.post("/api/v1/auth/login", json={"token": TOKEN})
        assert r.status_code == 200
        assert auth.MEDIA_COOKIE in r.cookies
        # the cookie (sent automatically by <img>/<video>) unlocks /media but not the API
        assert (await c.get(media_file)).text == "hello"
        assert (await c.get("/api/v1/models")).status_code == 401


async def test_media_signed_url_and_expiry(token, media_file):
    async with client() as c:
        assert (await c.get(media_file, headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
        signed = auth.sign_media_url(media_file, ttl_seconds=60)
        assert (await c.get(signed)).text == "hello"
        other = auth.sign_media_url("/media/authtest/other.txt", ttl_seconds=60)
        assert (await c.get(media_file + "?" + other.split("?", 1)[1])).status_code == 401  # path-bound
        expired = auth.sign_media_url(media_file, ttl_seconds=-1)
        assert (await c.get(expired)).status_code == 401


def test_cookie_ticket_expires(token):
    v = auth.media_ticket(ttl_seconds=-5)
    assert not auth.valid_ticket(v)
    assert auth.valid_ticket(auth.media_ticket(ttl_seconds=60))
    assert not auth.valid_ticket(f"{int(time.time()) + 60}.deadbeef")
