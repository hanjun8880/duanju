"""Which reference stills a shot in a given episode binds for a look-dev asset. Single source of truth for the
storyboard/render side: never read ref_urls directly (that list is unreviewed candidates).

meta.looks: [{"name": "婚纱", "episodes": [start, end], "locked": [url, ...]}]  (inclusive range, ≤3 urls)
"""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models import LookdevAsset

MAX_LOCKED = 3


def look_covers(look: dict[str, Any], episode: int) -> bool:
    eps = look.get("episodes") or []
    if len(eps) != 2:
        return False
    try:
        return int(eps[0]) <= episode <= int(eps[1])
    except (TypeError, ValueError):
        return False


def _not_expired(asset: LookdevAsset, url: str) -> bool:
    """A pure-text-to-image Seedream still is only guaranteed valid for lookdev.api.REF_VALID_DAYS
    (Seedance refuses real-photo references, so this is the only kind of still Seedance can bind)."""
    m = (asset.meta.get("ref_meta") or {}).get(url)
    if not m or not m.get("expires_at"):
        return True
    return datetime.fromisoformat(m["expires_at"]) > datetime.now(UTC)


async def refs_for_episode(session: AsyncSession, asset: LookdevAsset, episode: int) -> list[str]:
    """Episode look (range covers the episode, has locked stills) > asset lock > first candidate > nothing.
    Remix compliance (domains/remix/compliance.py): a character taken from external footage
    (meta.redesign_required) binds nothing until it has been redesigned.
    `session` is kept in the signature so callers stay stable if looks move to their own table."""
    if (asset.meta or {}).get("redesign_required"):
        return []
    for look in (asset.meta or {}).get("looks") or []:
        if isinstance(look, dict) and look.get("locked") and look_covers(look, episode):
            urls = [str(u) for u in look["locked"] if _not_expired(asset, str(u))][:MAX_LOCKED]
            if urls:
                return urls
    if asset.locked_ref_urls:
        urls = [str(u) for u in asset.locked_ref_urls if _not_expired(asset, str(u))][:MAX_LOCKED]
        if urls:
            return urls
    return [str(u) for u in asset.ref_urls if _not_expired(asset, str(u))][:1]
