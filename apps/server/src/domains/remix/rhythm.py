"""Rhythm table of the source episodes, measured from the local cut (zero token).

Stored as series_bible_json.rhythm so rewrite/storyboard prompts can match the original pacing. It is fixed
once measured, so it does not disturb the byte-stable cached bible prefix.
"""
from typing import Any

HOOK_WINDOW = 3.0  # seconds: the opening conflict must land inside this
CLIMAX_RATIO = 0.5  # a cut shorter than half the episode average counts as a rapid cut
CLIMAX_RUN = 2  # this many rapid cuts in a row = a climax beat


def _dur(s: dict[str, Any]) -> float:
    return max(0.0, float(s["end_time"]) - float(s["start_time"]))


def episode_rhythm(shots: list[dict[str, Any]], episode_number: int) -> dict[str, Any]:
    n = len(shots)
    total = round(sum(_dur(s) for s in shots), 2)
    avg = round(total / n, 2) if n else 0.0
    hooks: list[dict[str, Any]] = []
    opening = next((s for s in shots if float(s["start_time"]) < HOOK_WINDOW
                    and (s.get("dialogue") or float(s["end_time"]) <= HOOK_WINDOW)), None)
    if opening is not None:
        hooks.append({"kind": "opening", "shot_order": opening["shot_order"], "t": float(opening["start_time"])})
    run: list[dict[str, Any]] = []
    for s in [*shots, None]:
        if s is not None and avg and _dur(s) < avg * CLIMAX_RATIO:
            run.append(s)
            continue
        if len(run) >= CLIMAX_RUN:
            hooks.append({"kind": "climax", "shot_order": run[0]["shot_order"], "t": float(run[0]["start_time"])})
        run = []
    if n >= 2:
        last = shots[-1]
        hooks.append({"kind": "cliffhanger", "shot_order": last["shot_order"], "t": float(last["start_time"])})
    return {"episode_number": episode_number, "shot_count": n, "total_sec": total, "avg_shot_sec": avg,
            "cuts_per_min": round(n / total * 60, 1) if total else 0.0,
            "opening_hook_in_3s": opening is not None, "hooks": hooks}


def series_rhythm(per_episode: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    eps = [episode_rhythm(per_episode[k], k) for k in sorted(per_episode)]
    shots = sum(e["shot_count"] for e in eps)
    secs = sum(e["total_sec"] for e in eps)
    k = len(eps) or 1
    return {"episodes": eps, "avg_shot_sec": round(secs / shots, 2) if shots else 0.0,
            "avg_shots_per_episode": round(shots / k, 1), "avg_episode_sec": round(secs / k, 1)}


def with_rhythm(bible: dict[str, Any], rhythm: dict[str, Any]) -> dict[str, Any]:
    return {**bible, "rhythm": rhythm}


def rhythm_brief(rhythm: dict[str, Any]) -> str:
    """One paragraph for rewrite prompts: keep the original's pacing."""
    if not rhythm.get("episodes"):
        return ""
    hooked = sum(1 for e in rhythm["episodes"] if e["opening_hook_in_3s"])
    climaxes = sum(1 for e in rhythm["episodes"] for h in e["hooks"] if h["kind"] == "climax")
    return (f"原片节奏：共 {len(rhythm['episodes'])} 集，平均每集 {rhythm['avg_shots_per_episode']} 镜、"
            f"约 {rhythm['avg_episode_sec']} 秒，平均镜长 {rhythm['avg_shot_sec']} 秒；"
            f"{hooked} 集在开场 3 秒内给出钩子，全剧 {climaxes} 处快切高潮，每集结尾留悬念镜。改编时保持相近节奏。")
