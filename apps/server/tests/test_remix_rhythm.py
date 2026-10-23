"""Per-episode rhythm table from the local cut (no model call) -> series_bible_json.rhythm."""
import shutil
import subprocess

import pytest

from src.domains.remix import rhythm
from src.domains.script.video_remix import extract_keyframe


def cut(spans: list[tuple[float, float, str]]) -> list[dict]:
    return [{"shot_order": i, "start_time": a, "end_time": b, "duration": max(4, round(b - a)), "keyframe_path": "",
             "dialogue": d} for i, (a, b, d) in enumerate(spans, 1)]


EP = cut([(0, 2.0, "你凭什么？"), (2.0, 8.0, ""), (8.0, 9.0, ""), (9.0, 10.0, "滚！"), (10.0, 11.0, ""),
          (11.0, 20.0, ""), (20.0, 24.0, "原来是你……")])


def test_episode_rhythm_counts_real_durations_not_clamped():
    r = rhythm.episode_rhythm(EP, episode_number=1)
    assert r["episode_number"] == 1 and r["shot_count"] == 7
    assert r["total_sec"] == 24.0 and r["avg_shot_sec"] == pytest.approx(24 / 7, abs=0.01)
    assert r["cuts_per_min"] == pytest.approx(7 / 24 * 60, abs=0.1)
    assert r["opening_hook_in_3s"] is True


def test_hooks_opening_climax_cliffhanger():
    hooks = rhythm.episode_rhythm(EP, 1)["hooks"]
    assert hooks[0] == {"kind": "opening", "shot_order": 1, "t": 0.0}
    assert {"kind": "climax", "shot_order": 3, "t": 8.0} in hooks  # 3 short cuts in a row
    assert hooks[-1] == {"kind": "cliffhanger", "shot_order": 7, "t": 20.0}


def test_slow_open_has_no_3s_hook():
    r = rhythm.episode_rhythm(cut([(0, 9.0, ""), (9.0, 15.0, "")]), 2)
    assert r["opening_hook_in_3s"] is False
    assert [h["kind"] for h in r["hooks"]] == ["cliffhanger"]
    assert rhythm.episode_rhythm([], 3)["shot_count"] == 0


def test_series_rhythm_into_bible_without_touching_locked_assets():
    s = rhythm.series_rhythm({2: cut([(0, 4.0, "a"), (4.0, 10.0, "")]), 1: EP})
    assert [e["episode_number"] for e in s["episodes"]] == [1, 2]
    assert s["avg_shots_per_episode"] == 4.5 and s["avg_episode_sec"] == 17.0
    bible = {"title": "t", "characters": [{"name": "林"}]}
    out = rhythm.with_rhythm(bible, s)
    assert out["rhythm"] == s and out["characters"] == bible["characters"] and "rhythm" not in bible
    brief = rhythm.rhythm_brief(s)
    assert "4.5" in brief and "镜" in brief and "3 秒" in brief


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_keyframes_are_shrunk_before_any_vision_call(tmp_path):
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=1080x1920:r=25:d=1", "-pix_fmt", "yuv420p",
                    str(src)], check=True, capture_output=True)
    out = tmp_path / "k.jpg"
    extract_keyframe(str(src), 0.5, str(out))
    wh = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                         str(out)], capture_output=True, text=True, check=True).stdout.strip()
    assert wh == "432,768"  # long edge 768: ~443 image tokens instead of ~1500
