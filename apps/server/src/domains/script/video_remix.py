"""Local zero-token remix deconstruction: FFmpeg + PySceneDetect + faster-whisper (spec 路径二).

Nothing here touches a cloud API. Output feeds one batched VLM call later (1 keyframe per shot).
"""
import subprocess
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel
from scenedetect import ContentDetector, detect


def align_dialogue(transcript: list[dict[str, Any]], s_sec: float, e_sec: float) -> str:
    return " ".join(t["text"] for t in transcript if not (t["end"] <= s_sec or t["start"] >= e_sec))


class LocalVideoDeconstructor:
    def __init__(self, whisper_size: str = "large-v3", device: str = "auto", compute_type: str = "default"):
        # spec used cuda/float16; "auto"/"default" picks GPU fp16 when present, CPU int8/fp32 otherwise
        self.whisper = WhisperModel(whisper_size, device=device, compute_type=compute_type)

    def deconstruct_for_remix(self, video_path: str, output_dir: str) -> list[dict[str, Any]]:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        wav_path = out_path / "audio_16k.wav"

        # 1. FFmpeg 单次扫描剥离 16kHz 单声道音频
        subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                        str(wav_path)], check=True, capture_output=True)

        # 2. PySceneDetect HSV 自适应内容切点检测
        scene_list = detect(video_path, ContentDetector(threshold=27.0, min_scene_len=15))

        # 3. faster-whisper 本地转写 (segments is a lazy generator: materialise once)
        segments, _ = self.whisper.transcribe(str(wav_path), word_timestamps=True)
        transcript = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]

        # 4. 每个镜头提取中位关键帧并对齐台词
        shots: list[dict[str, Any]] = []
        for idx, (start_tc, end_tc) in enumerate(scene_list):
            s_sec, e_sec = start_tc.seconds, end_tc.seconds
            kf_file = out_path / f"shot_{idx:04d}_keyframe.jpg"
            subprocess.run(["ffmpeg", "-y", "-ss", str((s_sec + e_sec) / 2.0), "-i", video_path, "-frames:v", "1",
                            "-q:v", "2", str(kf_file)], check=True, capture_output=True)
            shots.append({
                "shot_order": idx + 1,
                "start_time": round(s_sec, 2),
                "end_time": round(e_sec, 2),
                "duration": max(4, min(30, round(e_sec - s_sec))),
                "keyframe_path": str(kf_file),
                "dialogue": align_dialogue(transcript, s_sec, e_sec),
            })
        return shots
