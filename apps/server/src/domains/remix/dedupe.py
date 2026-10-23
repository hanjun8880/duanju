"""Remix cost control before any cloud call (zero token):

1. dHash perceptual hash: adjacent shots whose keyframes look the same (jump cuts, held frames, re-cuts of one
   angle) send one image; the skipped shots reuse that description.
2. Vision estimate: images / batches / tokens / USD before the director clicks "识别".
3. Deconstruct cache keyed by the video's sha256: the same file (re-upload, rename) is never cut or
   transcribed twice, and keyframe descriptions are cached per (sha256, model).

Prices below are third-party placeholders (UNVERIFIED); an unknown model reports usd=None instead of guessing.
"""
import hashlib
import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2

from src.config import settings

THRESHOLD = 6  # max differing bits of 64 for "same picture"
COLOUR_DELTA = 24.0  # mean BGR distance above which two frames differ even with equal dHash (flat frames)
BATCH = 40  # images per vision request (well under the per-request image limit)
PER_SHOT_TEXT_TOKENS = 40  # "镜头 n（a-b s）台词：…" block
PER_BATCH_TOKENS = 300  # system rules + closing instruction
PER_SHOT_OUTPUT_TOKENS = 120  # one description
MAX_LONG_EDGE, MAX_PIXELS = 1568, 1_150_000  # Claude downsizes larger images before counting tokens
# (input, output) USD per 1M tokens, UNVERIFIED: one price table for estimate and ledger (settings.llm_prices)
VISION_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    m: (p.get("input", 0.0), p.get("output", 0.0)) for m, p in settings.llm_prices.items()}


def _gray_small(path: str | Path, size: int) -> Any:
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"cannot read image {path}")
    return img, cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (size + 1, size), interpolation=cv2.INTER_AREA)


def dhash(path: str | Path, size: int = 8) -> int:
    """Difference hash: each bit says whether a pixel is brighter than its right neighbour (64 bits)."""
    _, g = _gray_small(path, size)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int(sum(1 << i for i, b in enumerate(bits) if b))


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _mean_colour(path: str | Path) -> tuple[float, float, float]:
    img, _ = _gray_small(path, 8)
    b, g, r = (float(x) for x in img.reshape(-1, 3).mean(axis=0))
    return b, g, r


def _same(a: tuple[int, tuple[float, ...]], b: tuple[int, tuple[float, ...]], threshold: int) -> bool:
    return hamming(a[0], b[0]) <= threshold and math.dist(a[1], b[1]) <= COLOUR_DELTA


def dedupe_adjacent(shots: list[dict[str, Any]], threshold: int = THRESHOLD
                    ) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """Keep the first of each run of look-alike neighbours. Returns (kept shots with 'dhash' + 'covers',
    alias {skipped shot_order: kept shot_order}). Non-adjacent repeats are kept: a return to an angle is a beat."""
    kept: list[dict[str, Any]] = []
    alias: dict[int, int] = {}
    prev: tuple[int, tuple[float, ...]] | None = None
    for s in shots:
        sig = (dhash(s["keyframe_path"]), _mean_colour(s["keyframe_path"]))
        if prev is not None and kept and _same(prev, sig, threshold):
            head = kept[-1]
            alias[s["shot_order"]] = head["shot_order"]
            head["covers"].append(s["shot_order"])
        else:
            kept.append({**s, "dhash": f"{sig[0]:016x}", "covers": [s["shot_order"]]})
        prev = sig
    return kept, alias


def expand_notes(notes: dict[int, str], alias: dict[int, int]) -> dict[int, str]:
    return {**notes, **{skipped: notes[head] for skipped, head in alias.items() if head in notes}}


def image_tokens(path: str | Path) -> int:
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"cannot read image {path}")
    h, w = img.shape[:2]
    scale = min(1.0, MAX_LONG_EDGE / max(w, h), math.sqrt(MAX_PIXELS / (w * h)))
    return math.ceil(int(w * scale) * int(h * scale) / 750)


def estimate_vision(paths: list[str], model: str, batch: int = BATCH) -> dict[str, Any]:
    n = len(paths)
    batches = math.ceil(n / batch) if n else 0
    tokens_in = sum(image_tokens(p) for p in paths) + n * PER_SHOT_TEXT_TOKENS + batches * PER_BATCH_TOKENS
    tokens_out = n * PER_SHOT_OUTPUT_TOKENS
    price = VISION_USD_PER_MTOK.get(model)
    usd = round(tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1], 4) if price else None
    return {"model": model, "images": n, "batches": batches, "input_tokens": tokens_in,
            "output_tokens": tokens_out, "usd": usd if n else 0, "price_verified": False}


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)  # atomic: a half-written cache is never read


class DeconstructCache:
    """<root>/<sha256>/frames/*.jpg + shots.json (written last = complete) + notes-<model>.json."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def dir(self, sha: str) -> Path:
        return self.root / sha

    def get(self, sha: str) -> list[dict[str, Any]] | None:
        f = self.dir(sha) / "shots.json"
        return json.loads(f.read_text()) if f.is_file() else None

    def put(self, sha: str, shots: list[dict[str, Any]]) -> None:
        _write_json(self.dir(sha) / "shots.json", shots)

    def get_notes(self, sha: str, model: str) -> dict[int, str] | None:
        f = self.dir(sha) / f"notes-{model}.json"
        return {int(k): v for k, v in json.loads(f.read_text()).items()} if f.is_file() else None

    def put_notes(self, sha: str, model: str, notes: dict[int, str]) -> None:
        self.dir(sha).mkdir(parents=True, exist_ok=True)
        _write_json(self.dir(sha) / f"notes-{model}.json", {str(k): v for k, v in notes.items()})


def deconstruct_cached(video_path: str, cache: DeconstructCache,
                       deconstruct: Callable[[str, str], list[dict[str, Any]]]
                       ) -> tuple[str, list[dict[str, Any]], bool]:
    """(sha256, shots, cache_hit). On a miss the deconstructor writes its frames straight into the cache dir."""
    sha = file_sha256(video_path)
    hit = cache.get(sha)
    if hit is not None:
        return sha, hit, True
    out = cache.dir(sha) / "frames"
    out.mkdir(parents=True, exist_ok=True)
    shots = deconstruct(video_path, str(out))
    cache.put(sha, shots)
    return sha, shots, False
