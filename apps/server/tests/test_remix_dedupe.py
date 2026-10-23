"""Remix cost control: dHash adjacent-frame dedupe, vision-cost estimate, sha256 deconstruct cache.
Real images rendered by FFmpeg; nothing mocked."""
import shutil
import subprocess
from pathlib import Path

import pytest

from src.domains.remix import dedupe

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def frame(tmp: Path, name: str, src: str, vf: str | None = None, size: str = "320x240") -> str:
    out = tmp / f"{name}.jpg"
    sep = ":" if "=" in src else "="
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"{src}{sep}s={size}", "-frames:v", "1"]
    subprocess.run([*cmd, *(["-vf", vf] if vf else []), str(out)], check=True, capture_output=True)
    return str(out)


def shot(order: int, path: str, dialogue: str = "") -> dict:
    return {"shot_order": order, "start_time": (order - 1) * 4.0, "end_time": order * 4.0, "duration": 4,
            "keyframe_path": path, "dialogue": dialogue}


def test_dhash_near_duplicate_vs_different(tmp_path):
    a = dedupe.dhash(frame(tmp_path, "a", "testsrc"))
    a2 = dedupe.dhash(frame(tmp_path, "a2", "testsrc", "eq=brightness=0.04"))
    b = dedupe.dhash(frame(tmp_path, "b", "mandelbrot"))
    assert 0 <= a < 2 ** 64
    assert dedupe.hamming(a, a2) <= dedupe.THRESHOLD
    assert dedupe.hamming(a, b) > dedupe.THRESHOLD


def test_dedupe_only_merges_adjacent_similar(tmp_path):
    t = frame(tmp_path, "t", "testsrc")
    t2 = frame(tmp_path, "t2", "testsrc", "eq=brightness=0.04")
    m = frame(tmp_path, "m", "mandelbrot")
    t3 = frame(tmp_path, "t3", "testsrc")
    shots = [shot(1, t, "你来了"), shot(2, t2, "嗯"), shot(3, m), shot(4, t3)]
    kept, alias = dedupe.dedupe_adjacent(shots)
    assert [s["shot_order"] for s in kept] == [1, 3, 4]  # 4 looks like 1 but is not adjacent: a new beat
    assert alias == {2: 1}
    assert kept[0]["covers"] == [1, 2]
    notes = dedupe.expand_notes({1: "画面A", 3: "画面B", 4: "画面C"}, alias)
    assert notes == {1: "画面A", 2: "画面A", 3: "画面B", 4: "画面C"}


def test_flat_colour_frames_are_not_merged(tmp_path):
    """Solid frames all share dHash 0; the mean-colour guard keeps a red->green->blue cut apart."""
    shots = [shot(i, frame(tmp_path, c, f"color=c={c}")) for i, c in enumerate(["red", "green", "blue"], 1)]
    kept, alias = dedupe.dedupe_adjacent(shots)
    assert len(kept) == 3 and alias == {}


def test_estimate_counts_images_batches_and_usd(tmp_path):
    p = frame(tmp_path, "big", "testsrc", size="1080x1920")
    small = frame(tmp_path, "small", "testsrc", size="432x768")
    assert dedupe.image_tokens(p) > dedupe.image_tokens(small)
    assert dedupe.image_tokens(small) == -(-432 * 768 // 750)
    est = dedupe.estimate_vision([small] * 85, "claude-sonnet-5-5")
    assert est["images"] == 85 and est["batches"] == 3
    assert est["input_tokens"] > 85 * dedupe.image_tokens(small)
    in_price, out_price = dedupe.VISION_USD_PER_MTOK["claude-sonnet-5-5"]
    expect = est["input_tokens"] / 1e6 * in_price + est["output_tokens"] / 1e6 * out_price
    assert est["usd"] == pytest.approx(round(expect, 4))
    assert dedupe.estimate_vision([], "claude-sonnet-5-5")["usd"] == 0
    assert dedupe.estimate_vision([small], "unknown-model")["usd"] is None  # no price: say so, never guess


def test_sha256_cache_skips_second_deconstruct(tmp_path):
    video = tmp_path / "ep1.mp4"
    video.write_bytes(b"not really a video, only the bytes matter")
    calls: list[str] = []

    def fake_deconstruct(path: str, out_dir: str) -> list[dict]:
        calls.append(path)
        kf = Path(out_dir) / "shot_0000_keyframe.jpg"
        kf.write_bytes(b"jpg")
        return [shot(1, str(kf), "台词")]

    cache = dedupe.DeconstructCache(tmp_path / "cache")
    sha, shots, hit = dedupe.deconstruct_cached(str(video), cache, fake_deconstruct)
    assert sha == dedupe.file_sha256(str(video)) and len(sha) == 64 and not hit
    copy = tmp_path / "renamed.mp4"
    copy.write_bytes(video.read_bytes())
    sha2, shots2, hit2 = dedupe.deconstruct_cached(str(copy), cache, fake_deconstruct)
    assert (sha2, shots2, hit2) == (sha, shots, True)
    assert calls == [str(video)]  # same bytes under another name: zero recompute
    assert Path(shots2[0]["keyframe_path"]).is_file() and str(cache.root) in shots2[0]["keyframe_path"]
    cache.put_notes(sha, "claude-sonnet-5-5", {1: "画面"})
    assert cache.get_notes(sha, "claude-sonnet-5-5") == {1: "画面"}
    assert cache.get_notes(sha, "other-model") is None
