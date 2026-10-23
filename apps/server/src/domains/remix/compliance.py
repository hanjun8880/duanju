"""Remix compliance: characters taken from someone else's footage are never shot with the original look.

External source -> every character card is created with meta.redesign_required=true and no references, so the
director must run a fresh look-dev before any shot can bind it. Prompts still go through safety.check_prompt
(see modes.rewrite). Own-project forks are exempt: those looks are the user's own.
"""
from typing import Any

from src.domains.script.script_engine import SeriesPlan
from src.models import LookdevAsset

MAX_BOUND = 3  # same cap as lookdev locked refs


def redesign_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {**meta, "redesign_required": True, "source": "external_video", "likeness_consent": False}


def require_redesign(a: LookdevAsset) -> None:
    a.ref_urls, a.locked_ref_urls, a.face_embedding_url = [], [], None
    a.meta = redesign_meta(dict(a.meta or {}))


def bindable_refs(a: LookdevAsset) -> list[str]:
    """What a shot may bind for this asset (any episode without a per-episode look): nothing while a redesign
    is pending, else the same rule as lookdev.refs.refs_for_episode (locked ≤3, else the first candidate)."""
    if (a.meta or {}).get("redesign_required"):
        return []
    return list(a.locked_ref_urls or [])[:MAX_BOUND] or list(a.ref_urls or [])[:1]


def mark_redesigned(a: LookdevAsset) -> None:
    """Refs were cleared when the flag was set, so any refs present now come from the new look-dev."""
    if not (a.locked_ref_urls or a.ref_urls):
        raise ValueError("先重新定妆（生成或上传新的参考图）再解除 redesign_required")
    a.meta = {**(a.meta or {}), "redesign_required": False}


def plan_assets(plan: SeriesPlan, external: bool, project_id: Any = None) -> list[LookdevAsset]:
    cards = [("character", c.name, c.role_tag, c.visual_prompt) for c in plan.characters] + \
        [("prop", x.name, None, x.visual_prompt) for x in plan.props] + \
        [("scene", x.name, None, x.visual_prompt) for x in plan.scenes]
    out = []
    for t, n, r, v in cards:
        a = LookdevAsset(project_id=project_id, asset_type=t, name=n, role_tag=r, visual_prompt=v, ref_urls=[],
                         locked_ref_urls=[], meta={"likeness_consent": False, "from_plan": True, "remix": True})
        if external and t == "character":
            require_redesign(a)
        out.append(a)
    return out
