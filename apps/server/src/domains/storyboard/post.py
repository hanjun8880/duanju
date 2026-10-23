"""Zero-credit local post-processing (L0 animatic, L3 upscale / face fix, episode assembly)."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
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
                     size: tuple[int, int] = (1080, 1920), lines: list[Line | None] | None = None,
                     audio_mode: str = "native", bgm: str | None = None, subtitles: bool = True) -> str:
    """Ordered episode (or whole-series) cut: an OTIO timeline for NLE round-trip + an FFmpeg master.
    Every clip is normalized first because clips come from different vendors / the local animatic.
    lines (one per clip, None = no dialogue) / bgm switch on the dialogue-track + subtitle + BGM pass."""
    if lines is not None and len(lines) != len(clips):
        raise ValueError("lines must align one-to-one with clips")
    with tempfile.TemporaryDirectory() as tmp:
        norm = [normalize(c, str(Path(tmp) / f"{i:04d}.mp4"), size) for i, c in enumerate(clips)]
        tl = otio.schema.Timeline(name=name)
        track = otio.schema.Track(name="V1")
        tl.tracks.append(track)
        durs: list[float] = []
        for c, n in zip(clips, norm, strict=True):
            dur = probe_duration(n)
            durs.append(dur)
            track.append(otio.schema.Clip(
                name=Path(c).stem, media_reference=otio.schema.ExternalReference(target_url=str(Path(c).resolve())),
                source_range=otio.opentime.TimeRange(otio.opentime.RationalTime(0, FPS),
                                                     otio.opentime.RationalTime(round(dur * FPS), FPS))))
        otio.adapters.write_to_file(tl, otio_path)
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{n}'\n" for n in norm))
        post_pass = lines is not None or bgm is not None or audio_mode != "native"
        cat = str(Path(tmp) / "cat.mp4") if post_pass else out
        _ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", cat])
        if post_pass:
            starts = [sum(durs[:i]) for i in range(len(durs))]
            mix_master(cat, out, size, starts, durs, lines or [None] * len(clips), audio_mode, bgm, subtitles)
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


# ---------------------------------------------------------------- dialogue track, subtitles, BGM (episode master)
AUDIO_MODES = ("tts", "native", "mix")  # dialogue track: TTS only / vendor native audio only / both
DIALOGUE_LEAD = 0.1  # seconds after the shot cut before the line starts
NATIVE_UNDER_TTS = 0.35  # native-audio gain in "mix" (ambience under the voiced line)
BGM_GAIN = 0.3


@dataclass(frozen=True)
class Line:
    """One shot's dialogue: the spoken audio file (None = text only) and the subtitle text."""
    text: str
    audio: str | None = None
    subtitle: bool = True  # False when the clip already has burned-in text (local animatic)


def wrap_cjk(text: str, max_chars: int) -> str:
    """libass only breaks at spaces, so CJK lines get explicit \\N breaks every max_chars characters."""
    out, line = [], ""
    for ch in text.replace("\n", " ").strip():
        line += ch
        if len(line) >= max_chars:
            out.append(line.strip())
            line = ""
    if line.strip():
        out.append(line.strip())
    return r"\N".join(out)


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_ass(cues: list[tuple[float, float, str]], size: tuple[int, int], path: str) -> str:
    """Vertical-first ASS: font scales with frame width, bottom margin clears short-video app UI."""
    w, h = size
    fs = max(round(min(w, h) * 0.06), 16)
    margin_v, margin_lr = round(h * (0.14 if h > w else 0.08)), round(w * 0.06)
    max_chars = max((w - 2 * margin_lr) // fs, 6)
    esc = [(a, b, wrap_cjk(t.replace("{", "(").replace("}", ")"), max_chars)) for a, b, t in cues]
    Path(path).write_text(
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,{SUB_FONT},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(round(fs / 14), 2)},0,2,{margin_lr},{margin_lr},{margin_v},1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "".join(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Default,,0,0,0,,{t}\n" for a, b, t in esc),
        encoding="utf-8")
    return path


def mix_master(video: str, out: str, size: tuple[int, int], starts: list[float], durations: list[float],
               lines: list[Line | None], audio_mode: str = "tts", bgm: str | None = None,
               subtitles: bool = True) -> str:
    """Lay each shot's line at its shot start (+DIALOGUE_LEAD), pick the dialogue track mode, duck an optional
    looping BGM under the voice (sidechain), and burn ASS subtitles. Video is copied when nothing is burned."""
    if audio_mode not in AUDIO_MODES:
        raise ValueError(f"audio_mode must be one of {AUDIO_MODES}")
    total = sum(durations)
    args, graph, n_in = ["-i", video], [], 1
    dlg_labels, cues = [], []
    for i, ln in enumerate(lines):
        if ln is None:
            continue
        start, shot_end = starts[i] + DIALOGUE_LEAD, starts[i] + durations[i]
        end = shot_end
        if ln.audio and audio_mode != "native":
            args += ["-i", ln.audio]
            ms = round(start * 1000)
            graph.append(f"[{n_in}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                         f"adelay={ms}:all=1[d{n_in}]")
            dlg_labels.append(f"[d{n_in}]")
            end = max(shot_end, start + probe_duration(ln.audio))
            n_in += 1
        if ln.subtitle and ln.text:
            cues.append((start, min(end, total), ln.text))
    norm0 = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if dlg_labels:
        graph.append("".join(dlg_labels) + f"amix=inputs={len(dlg_labels)}:normalize=0:duration=longest,"
                     f"apad,atrim=0:{total}[dlg]")
    has_dlg = bool(dlg_labels)
    if audio_mode == "native" or (audio_mode == "mix" and not has_dlg):
        graph.append(f"[0:a]{norm0}[base]")
    elif audio_mode == "mix":
        graph.append(f"[0:a]{norm0},volume={NATIVE_UNDER_TTS}[nat]")
        graph.append("[dlg]asplit=2[dlgm][dlgsc]" if bgm else "[dlg]anull[dlgm]")
        graph.append("[nat][dlgm]amix=inputs=2:normalize=0:duration=first[base]")
    elif has_dlg:  # tts
        graph.append("[dlg]asplit=2[base][dlgsc]" if bgm else "[dlg]anull[base]")
    else:
        graph.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{total}[base]")
    final = "[base]"
    if bgm:
        args += ["-stream_loop", "-1", "-i", bgm]
        graph.append(f"[{n_in}:a]{norm0},volume={BGM_GAIN},atrim=0:{total}[bgm0]")
        if has_dlg and audio_mode != "native":
            graph.append("[bgm0][dlgsc]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=400[bgm]")
        else:
            graph.append("[bgm0]anull[bgm]")
        graph.append("[base][bgm]amix=inputs=2:normalize=0:duration=first[mixed]")
        final = "[mixed]"
    graph.append(f"{final}atrim=0:{total},alimiter=limit=0.95[aout]")
    with tempfile.TemporaryDirectory() as tmp:
        vmap, vcodec = ["-map", "0:v"], ["-c:v", "copy"]
        if subtitles and cues:
            ass = write_ass(cues, size, str(Path(tmp) / "subs.ass"))
            graph.append(f"[0:v]ass={ass}[vout]")
            vmap, vcodec = ["-map", "[vout]"], ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
        _ff([*args, "-filter_complex", ";".join(graph), *vmap, "-map", "[aout]", *vcodec,
             "-c:a", "aac", "-ar", "44100", "-ac", "2", "-t", f"{total:.3f}", "-movflags", "+faststart", out])
    return out
