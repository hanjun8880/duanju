"""Zero-credit local post-processing (L0 animatic, L3 upscale / face fix, episode assembly)."""
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import opentimelineio as otio

FPS = 25
SUB_FONT = os.environ.get("SUBTITLE_FONT", "WenQuanYi Zen Hei")


class ToolMissing(RuntimeError):
    pass


def _ff(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, capture_output=True)


def probe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                         check=True, capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def _srt_time(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def render_animatic(frames: list[dict[str, Any]], out: str, size: tuple[int, int] = (1080, 1920),
                    audio: str | None = None) -> str:
    """frames: [{image, duration, subtitle}] -> stills + burned-in dialogue (+ optional voice track)."""
    w, h = size
    args: list[str] = []
    chains, t, cues = [], 0.0, []
    for i, f in enumerate(frames):
        args += ["-loop", "1", "-t", str(f["duration"]), "-i", f["image"]]
        chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                      f"setsar=1,fps={FPS},format=yuv420p[v{i}]")
        if f.get("subtitle"):
            cues.append((t, t + f["duration"], f["subtitle"]))
        t += f["duration"]
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(len(frames))) + \
        f"concat=n={len(frames)}:v=1:a=0[cat]"
    with tempfile.TemporaryDirectory() as tmp:
        last = "[cat]"
        if cues:
            srt = Path(tmp) / "subs.srt"
            srt.write_text("\n".join(f"{n}\n{_srt_time(a)} --> {_srt_time(b)}\n{txt}\n"
                                     for n, (a, b, txt) in enumerate(cues, 1)), encoding="utf-8")
            graph += f";[cat]subtitles={srt}:force_style='FontName={SUB_FONT},FontSize=14,Outline=1'[out]"
            last = "[out]"
        args += ["-i", audio] if audio else []
        maps = ["-map", last] + (["-map", f"{len(frames)}:a", "-c:a", "aac"] if audio else [])
        _ff([*args, "-filter_complex", graph, *maps, "-t", str(t), "-c:v", "libx264", "-pix_fmt", "yuv420p", out])
    return out


SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:3": (1440, 1080),
         "3:4": (1080, 1440), "21:9": (2520, 1080)}


def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", path], check=True, capture_output=True, text=True).stdout
    return bool(out.strip())


def normalize(src: str, out: str, size: tuple[int, int]) -> str:
    """Same size / fps / pixel format / stereo track for every clip, so mixed vendor + animatic clips concat."""
    w, h = size
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
          f"fps={FPS},format=yuv420p")
    if has_audio(src):
        io = ["-i", src, "-map", "0:v:0", "-map", "0:a:0"]
    else:  # silent animatic: add a silent track so concat sees identical streams
        io = ["-i", src, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v:0", "-map", "1:a",
              "-shortest"]
    _ff([*io, "-vf", vf, "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-ar", "44100", "-ac", "2", out])
    return out


def assemble_episode(clips: list[str], out: str, otio_path: str, name: str,
                     size: tuple[int, int] = (1080, 1920)) -> str:
    """Ordered episode (or whole-series) cut: an OTIO timeline for NLE round-trip + an FFmpeg master.
    Every clip is normalized first because clips come from different vendors / the local animatic."""
    with tempfile.TemporaryDirectory() as tmp:
        norm = [normalize(c, str(Path(tmp) / f"{i:04d}.mp4"), size) for i, c in enumerate(clips)]
        tl = otio.schema.Timeline(name=name)
        track = otio.schema.Track(name="V1")
        tl.tracks.append(track)
        for c, n in zip(clips, norm, strict=True):
            dur = probe_duration(n)
            track.append(otio.schema.Clip(
                name=Path(c).stem, media_reference=otio.schema.ExternalReference(target_url=str(Path(c).resolve())),
                source_range=otio.opentime.TimeRange(otio.opentime.RationalTime(0, FPS),
                                                     otio.opentime.RationalTime(round(dur * FPS), FPS))))
        otio.adapters.write_to_file(tl, otio_path)
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{n}'\n" for n in norm))
        _ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", out])
    return out


def upscale_4k(src: str, out: str) -> str:
    """Real-ESRGAN x4plus via realesrgan-ncnn-vulkan (REALESRGAN_BIN). NOT_TESTED: binary absent in CI sandbox."""
    binary = os.environ.get("REALESRGAN_BIN")
    if not binary:
        raise ToolMissing("set REALESRGAN_BIN to a realesrgan-ncnn-vulkan executable")
    with tempfile.TemporaryDirectory() as tmp:
        src_dir, up_dir = Path(tmp, "in"), Path(tmp, "up")
        src_dir.mkdir()
        up_dir.mkdir()
        _ff(["-i", src, str(src_dir / "%06d.png")])
        subprocess.run([binary, "-i", str(src_dir), "-o", str(up_dir), "-n", "realesrgan-x4plus"], check=True)
        _ff(["-framerate", str(FPS), "-i", str(up_dir / "%06d.png"), "-i", src, "-map", "0:v", "-map", "1:a?",
             "-vf", "scale=-2:2160:flags=lanczos", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
             "-c:a", "copy", out])
    return out


def face_swap(src: str, face_ref: str, out: str) -> str:
    """Runs FACE_SWAP_CMD (e.g. an InsightFace/roop script) with {src} {face} {out}. Consent is enforced at the API.
    NOT_TESTED: no swap model weights in the sandbox."""
    tpl = os.environ.get("FACE_SWAP_CMD")
    if not tpl:
        raise ToolMissing("set FACE_SWAP_CMD, e.g. 'python swap.py --src {src} --face {face} --out {out}'")
    subprocess.run(shlex.split(tpl.format(src=src, face=face_ref, out=out)), check=True)
    return out
