"""Real local L0/L3 post-processing: FFmpeg animatic and OTIO episode assembly. No mocks."""
import json
import subprocess
from pathlib import Path

import opentimelineio as otio
import pytest

from src.domains.storyboard.post import (
    ToolMissing,
    assemble_episode,
    probe_duration,
    render_animatic,
    upscale_4k,
)


@pytest.fixture(scope="module")
def stills(tmp_path_factory) -> list[Path]:
    d = tmp_path_factory.mktemp("stills")
    out = []
    for i, c in enumerate(["0x3b2f2a", "0xc9b48a"]):
        p = d / f"s{i}.png"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={c}:s=540x960", "-frames:v", "1", str(p)],
                       check=True, capture_output=True)
        out.append(p)
    return out


def test_animatic_duration_and_subtitles(stills, tmp_path):
    out = tmp_path / "animatic.mp4"
    render_animatic([{"image": str(stills[0]), "duration": 4, "subtitle": "她回头，晨光落在怀表上"},
                     {"image": str(stills[1]), "duration": 5, "subtitle": "“你终于来了。”"}],
                    str(out), size=(540, 960))
    assert abs(probe_duration(str(out)) - 9.0) < 0.2
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                     capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (540, 960)


def test_assemble_episode_writes_otio_and_mp4(stills, tmp_path):
    clips = []
    for i, s in enumerate(stills):
        c = tmp_path / f"c{i}.mp4"
        render_animatic([{"image": str(s), "duration": 4 + i, "subtitle": ""}], str(c), size=(540, 960))
        clips.append(str(c))
    out, otio_path = tmp_path / "ep01.mp4", tmp_path / "ep01.otio"
    assemble_episode(clips, str(out), str(otio_path), name="EP01")
    assert abs(probe_duration(str(out)) - 9.0) < 0.3
    tl = otio.adapters.read_from_file(str(otio_path))
    assert tl.name == "EP01"
    assert [round(c.source_range.duration.to_seconds()) for c in tl.tracks[0]] == [4, 5]


def test_upscale_without_binary_is_reported_not_faked(tmp_path, monkeypatch):
    monkeypatch.delenv("REALESRGAN_BIN", raising=False)
    with pytest.raises(ToolMissing, match="REALESRGAN_BIN"):
        upscale_4k(str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"))
