"""Local media store under settings.storage_dir, served by FastAPI at /media.

shortcut: local disk instead of MinIO/S3 presigned URLs; fine for a single-operator workbench, swap for S3
before several machines share one storage.
"""
import base64
import mimetypes
import uuid
from pathlib import Path

import httpx

from src.config import settings

PREFIX = "/media/"


def root() -> Path:
    return Path(settings.storage_dir).resolve()


def local_path(url: str) -> Path | None:
    """'/media/x/y.png' -> absolute path inside storage, or None for anything else (remote URL, traversal)."""
    if not url.startswith(PREFIX):
        return None
    p = (root() / url[len(PREFIX):]).resolve()
    return p if root() in p.parents and p.is_file() else None


def url_for(path: Path) -> str:
    return PREFIX + str(path.resolve().relative_to(root()))


def data_url(url: str) -> str:
    """Vendors cannot reach localhost: inline our own images/audio as base64 (Ark accepts data: URLs)."""
    p = local_path(url)
    if p is None:
        return url
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


async def download(client: httpx.AsyncClient, url: str, sub: str, suffix: str) -> str:
    """Vendor output URLs expire (Ark: 24h), so keep our own copy. Returns the /media URL."""
    dest = root() / sub
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{uuid.uuid4().hex}{suffix}"
    async with client.stream("GET", url, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            async for chunk in r.aiter_bytes():
                f.write(chunk)
    return url_for(out)
