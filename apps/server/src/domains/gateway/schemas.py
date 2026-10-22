"""Job request contract + Pre-Flight Parameter Sanitizer (spec 机制四)."""
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

TaskType = Literal["image.t2i", "image.i2i", "video.i2v", "video.t2v", "video.edit", "video.extend",
                   "face.swap", "post.upscale"]


class MultimodalRefs(BaseModel):
    image_urls: list[str] = Field(default_factory=list, max_length=30)
    video_urls: list[str] = Field(default_factory=list, max_length=10)
    audio_urls: list[str] = Field(default_factory=list, max_length=10)

    def any(self) -> bool:
        return bool(self.image_urls or self.video_urls or self.audio_urls)


class JobCreateRequest(BaseModel):
    project_id: UUID
    shot_id: UUID | None = None
    task_type: TaskType
    model_key: str = Field(..., examples=["seedance_2_5_spicy", "wan3_spicy", "minimax_h3", "flux_uncensored"])
    prompt: str = Field(..., min_length=1)
    negative_prompt: str | None = None
    duration: int = Field(default=5, description="4 to 30 seconds, or -1 for adaptive video edit")
    resolution: Literal["480p", "720p", "1080p"] = "720p"
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"] = "9:16"
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    return_last_frame: bool = True
    generate_audio: bool = True
    refs: MultimodalRefs = Field(default_factory=MultimodalRefs)
    callback_url: str | None = None
    # additions beyond the spec contract:
    edit_source_seconds: int | None = Field(default=None, ge=1, le=600,
                                            description="video.edit bills input+output seconds")
    likeness_consent: bool = Field(default=False,
                                   description="operator declares fictional character or full likeness rights")

    @model_validator(mode="after")
    def enforce_seedance_2_5_constraints(self) -> "JobCreateRequest":
        # 规则 1: last_frame_url 必须与 first_frame_url 同时使用
        if self.last_frame_url and not self.first_frame_url:
            raise ValueError("last_frame_url cannot be used without first_frame_url.")
        # 规则 2: 首尾帧模式与多模态参考数组互斥, 保留首尾帧链式接力
        if self.first_frame_url and self.refs.any():
            self.refs = MultimodalRefs()
        if self.refs.audio_urls and not (self.refs.image_urls or self.refs.video_urls):
            raise ValueError("reference audio needs at least one image or video reference.")
        # 规则 3: 视频编辑模式强制 adaptive 宽高比与 -1 时长
        if self.task_type == "video.edit":
            self.aspect_ratio = "adaptive"
            self.duration = -1
            if not self.edit_source_seconds:
                raise ValueError("video.edit requires edit_source_seconds for billing.")
        elif not (4 <= self.duration <= 30):
            raise ValueError("Standard video generation duration must be between 4 and 30 seconds.")
        return self

    def billable_units(self) -> int:
        if self.task_type == "video.edit":
            assert self.edit_source_seconds is not None
            return 2 * self.edit_source_seconds  # adaptive output ~= input length
        if self.task_type.startswith("image."):
            return 1
        return self.duration


def preflight(req: JobCreateRequest) -> tuple[str, dict[str, Any]]:
    """Deterministic clean-up before any vendor call. Returns (effective task_type, canonical payload)."""
    task: str = req.task_type
    p: dict[str, Any] = {"prompt": req.prompt, "negative_prompt": req.negative_prompt,
                         "resolution": req.resolution, "aspect_ratio": req.aspect_ratio}
    if task.startswith("video."):
        p |= {"duration": req.duration, "generate_audio": req.generate_audio,
              "return_last_frame": req.return_last_frame}
    if req.first_frame_url:
        p |= {"first_frame_url": req.first_frame_url, "last_frame_url": req.last_frame_url}
    elif req.refs.any():
        p |= {"reference_image_urls": req.refs.image_urls or None, "reference_video_urls": req.refs.video_urls or None,
              "reference_audio_urls": req.refs.audio_urls or None, "omni_reference_task_type": "multi_reference"}
    elif task == "video.i2v":
        task = "video.t2v"  # 空参考自动降级为纯文生视频
    return task, {k: v for k, v in p.items() if v is not None}
