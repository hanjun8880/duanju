"""Dialogue track modes, alignment to shot starts, vertical ASS subtitles and BGM ducking. Real FFmpeg.
Native clip audio = 440 Hz, voiced line = 880 Hz, BGM = 220 Hz, so each layer is measured in its own band."""
import re
import subprocess
from pathlib import Path

import pytest

from src.domains.storyboard.post import (
    Line,
    assemble_episode,
    mix_master,
    probe_duration,
    wrap_cjk,
    write_ass,
)

SIZE = (360, 640)


def _run(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stderr


def clip(path: Path, color: str, dur: float, tone: int | None = 440) -> str:
    a = ["-f", "lavfi", "-i", f"sine=f={tone}:d={dur}:sample_rate=44100"] if tone else []
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d={dur}:r=25", *a, "-c:v", "libx264",
          "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if tone else []), "-shortest", str(path)])
    return str(path)


def tone(path: Path, f: int, dur: float) -> str:
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d={dur}:sample_rate=44100", str(path)])
    return str(path)


def band_db(path: str, start: float, dur: float, f: int) -> float:
    """Mean level (dB) of a narrow band around f in [start, start+dur]."""
    err = _run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", path, "-af",
                f"bandpass=f={f}:width_type=h:w=60,volumedetect", "-vn", "-f", "null", "-"])
    m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", err)
    assert m, err
    return -120.0 if m.group(1) == "-inf" else float(m.group(1))


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("dlg")
    return {"c1": clip(d / "c1.mp4", "0x203040", 2.0), "c2": clip(d / "c2.mp4", "0x203040", 3.0),
            "line": tone(d / "line.wav", 880, 1.0), "bgm": tone(d / "bgm.wav", 220, 1.5), "dir": d}


def ep(media, name, **kw) -> str:
    out = str(media["dir"] / f"{name}.mp4")
    assemble_episode([media["c1"], media["c2"]], out, str(media["dir"] / f"{name}.otio"), name, SIZE, **kw)
    return out


def test_tts_only_drops_native_and_aligns_line_to_shot_start(media):
    out = ep(media, "tts", lines=[None, Line("我一直都在。", media["line"])], audio_mode="tts", subtitles=False)
    assert abs(probe_duration(out) - 5.0) < 0.2
    assert band_db(out, 0.3, 1.2, 440) < -60  # native dropped
    assert band_db(out, 1.4, 0.5, 880) < -60  # nothing before shot 2 starts at 2.0s
    assert band_db(out, 2.3, 0.6, 880) > -40  # the line plays from the shot start (+lead)


def test_native_only_keeps_clip_audio_and_ignores_tts(media):
    out = ep(media, "native", lines=[None, Line("我一直都在。", media["line"])], audio_mode="native",
             subtitles=False)
    assert band_db(out, 0.3, 1.2, 440) > -40
    # 880 band only carries leakage from the 440 Hz native tone: same level with and without a line
    assert abs(band_db(out, 2.3, 0.6, 880) - band_db(out, 0.3, 0.6, 880)) < 3


def test_mix_keeps_native_under_the_line(media):
    out = ep(media, "mix", lines=[None, Line("我一直都在。", media["line"])], audio_mode="mix", subtitles=False)
    native_alone = band_db(media["c1"], 0.3, 1.2, 440)
    under = band_db(out, 0.3, 1.2, 440)
    assert under > -60 and under < native_alone - 6  # attenuated, not gone
    assert band_db(out, 2.3, 0.6, 880) > -40


def test_bgm_ducks_under_dialogue(media):
    out = ep(media, "bgm", lines=[None, Line("台词", media["line"])], audio_mode="tts", bgm=media["bgm"],
             subtitles=False)
    free, ducked = band_db(out, 0.5, 1.0, 220), band_db(out, 2.4, 0.5, 220)
    assert free > -50  # bgm loops (1.5s file) across the whole 5s
    assert band_db(out, 4.0, 0.8, 220) > -50
    assert ducked < free - 4


def test_silent_clip_tts_mode_without_lines_is_valid(media):
    c = clip(media["dir"] / "mute.mp4", "0x101010", 2.0, tone=None)
    out = str(media["dir"] / "mute_out.mp4")
    assemble_episode([c], out, str(media["dir"] / "mute.otio"), "M", SIZE, lines=[None], audio_mode="tts")
    assert abs(probe_duration(out) - 2.0) < 0.2


def _gray_frame(path: str, t: float) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf",
                           "crop=360:200:0:400", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          check=True, capture_output=True).stdout


def test_subtitles_burned_only_while_line_is_on(media):
    out = ep(media, "subs", lines=[None, Line("我一直都在，一直都在这里等你回来。", media["line"])], audio_mode="tts")
    before, during = _gray_frame(out, 1.0), _gray_frame(out, 2.6)
    assert max(before) - min(before) < 10  # flat background, no text
    assert max(during) - min(during) > 120  # white text with black outline


def test_animatic_lines_skip_subtitles(media):
    out = ep(media, "nosub", lines=[None, Line("我一直都在。", media["line"], subtitle=False)], audio_mode="tts")
    f = _gray_frame(out, 2.6)
    assert max(f) - min(f) < 10


def test_write_ass_vertical_layout(tmp_path):
    p = write_ass([(0.1, 2.0, "你终于来了，我等了你整整三年。{\\b1}")], (1080, 1920), str(tmp_path / "s.ass"))
    s = Path(p).read_text(encoding="utf-8")
    assert "PlayResX: 1080" in s and "PlayResY: 1920" in s and "WenQuanYi Zen Hei" in s
    assert "Dialogue: 0,0:00:00.10,0:00:02.00,Default" in s
    assert "{\\b1}" not in s  # user text cannot inject override tags
    style = next(x for x in s.splitlines() if x.startswith("Style:")).split(",")
    assert int(style[2]) == 65 and int(style[-2]) >= 250  # font ~6% width, bottom margin clears app UI


def test_wrap_cjk():
    assert wrap_cjk("一二三四五六七", 3) == "一二三\\N四五六\\N七"
    assert wrap_cjk("短句", 10) == "短句"


def test_mix_master_rejects_unknown_mode(media):
    with pytest.raises(ValueError):
        mix_master(media["c1"], str(media["dir"] / "x.mp4"), SIZE, [0.0], [2.0], [None], audio_mode="loud")
