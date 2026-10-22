"""Real local pipeline: FFmpeg + PySceneDetect + faster-whisper(tiny, CPU). No mocks."""
import shutil
import subprocess
from pathlib import Path

import pytest

from src.domains.script.video_remix import LocalVideoDeconstructor, align_dialogue

DATA = Path(__file__).parent / "data"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


@pytest.fixture(scope="module")
def three_shot_video(tmp_path_factory) -> Path:
    """12s clip: red/green/blue hard cuts every 4s, with the JFK speech sample as audio."""
    out = tmp_path_factory.mktemp("remix") / "src.mp4"
    colors = ["red", "green", "blue"]
    inputs = [a for c in colors for a in ("-f", "lavfi", "-i", f"color=c={c}:s=320x240:r=25:d=4")]
    subprocess.run(["ffmpeg", "-y", *inputs, "-i", str(DATA / "jfk.flac"), "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True, capture_output=True)
    return out


def test_align_dialogue_overlap_rule():
    lines = [{"start": 0.0, "end": 2.0, "text": "a"}, {"start": 3.5, "end": 4.5, "text": "b"},
             {"start": 4.0, "end": 5.0, "text": "c"}]
    assert align_dialogue(lines, 0.0, 4.0) == "a b"
    assert align_dialogue(lines, 4.0, 8.0) == "b c"


def test_deconstruct_real_video(three_shot_video, tmp_path):
    d = LocalVideoDeconstructor(whisper_size="tiny", device="cpu")
    shots = d.deconstruct_for_remix(str(three_shot_video), str(tmp_path))
    assert [s["shot_order"] for s in shots] == [1, 2, 3]
    assert [round(s["start_time"]) for s in shots] == [0, 4, 8]
    for s in shots:
        assert Path(s["keyframe_path"]).stat().st_size > 0
        assert 4 <= s["duration"] <= 30
    assert (tmp_path / "audio_16k.wav").exists()
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels", "-of",
                            "csv=p=0", str(tmp_path / "audio_16k.wav")], capture_output=True, text=True,
                           check=True).stdout
    assert probe.strip() == "16000,1"
    text = " ".join(s["dialogue"] for s in shots).lower()
    assert "ask not" in text and "country" in text
