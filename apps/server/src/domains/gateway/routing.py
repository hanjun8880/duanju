"""Per-stage model assignment: every pipeline step asks here which model to use. Single source of truth.

Override any stage with env MODEL_<STAGE> (e.g. MODEL_SCRIPT=claude-opus-5-5) or at runtime through the admin
API (ledger team persists overrides). Cheap by default: only whole-series planning uses the top LLM, and
1080p video only for an explicit final render.
"""
import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class StageModel:
    stage: str
    kind: str  # llm | image | video | tts | vision
    model: str  # LLM model id, or model_catalog.model_key for image/video
    provider: str  # anthropic | openai_compat | ark | wan | minimax | volc_tts | minimax_tts | local
    resolution: str | None = None
    note: str = ""


# 专家选型 2026-10-23（Victor 拉的七个专家窗口，按岗位拆模型，价格对着官方价目页口头核实过，细节报告还在路上）
# 给出了比下面默认值更便宜的分工建议（GLM-5.3/Kimi K3/doubao-seed-2.1-lite/Seedance 2.0 mini/Wan2.2-flash/
# qwen-audio-tts 等）。这些模型/vendor 的调用路径都已经打通（script_engine.OpenAICompatLLM.for_vendor，
# model_catalog 新增的 seedream_5_flash/seedance_2_0_mini(_draft)/wan2_2_i2v_flash/keling_4_0 行，
# voice/providers.py 的 qwen TTS），但没有改下面这几行硬编码默认值——这些默认值被几十个测试用 Claude/Ark/Wan
# mock 死死钉住，改默认值等于同时要改掉那些测试的桩，风险和这次改动不成比例。真正想换成本更低的分工，
# 按专家选型表在部署环境设 MODEL_<STAGE>/PROVIDER_<STAGE>（例如 MODEL_PLAN=glm-5.3 PROVIDER_PLAN=zhipu）
# 或用 PUT /api/v1/admin/stages 持久化覆盖；具体每个 stage 对应哪组推荐值见 PROGRESS.md 的价目表。
STAGES: dict[str, StageModel] = {s.stage: s for s in [
    # 编剧
    StageModel("summarize", "llm", "claude-sonnet-5-5", "anthropic", note="长小说逐段摘要；可换更便宜的 openai_compat 模型"),
    StageModel("plan", "llm", "claude-opus-5-5", "anthropic", note="整剧规划：唯一默认用顶级模型的环节"),
    StageModel("script", "llm", "claude-sonnet-5-5", "anthropic", note="分集剧本"),
    StageModel("storyboard", "llm", "claude-sonnet-5-5", "anthropic", note="分镜表"),
    StageModel("reformat", "llm", "claude-sonnet-5-5", "anthropic", note="导入剧本整理格式"),
    # 二创
    StageModel("remix_vision", "vision", "claude-sonnet-5-5", "anthropic", note="原片关键帧识别"),
    StageModel("remix_rewrite", "llm", "claude-sonnet-5-5", "anthropic", note="二创改写"),
    # 定妆 / 关键帧（Seedance 不收真人照片做参考图；定妆必须走 Seedream 纯文生图出脸，30 天内有效，见 lookdev/refs.py）
    StageModel("lookdev_image", "image", "seedream_5_pro", "ark", note="定妆图/三视图/表情"),
    StageModel("keyframe_image", "image", "seedream_5_pro", "ark", note="每镜关键帧审批"),
    # 视频
    StageModel("video_draft", "video", "seedance_2_5_draft", "ark", "480p", "草稿"),
    StageModel("video_dialogue", "video", "seedance_2_5_spicy", "ark", "720p", "对白/人物特写"),
    StageModel("video_action", "video", "wan3_spicy", "wan", "720p", "动作"),
    StageModel("video_establishing", "video", "wan3_spicy", "wan", "720p", "空镜/环境"),
    StageModel("video_insert", "video", "wan3_spicy", "wan", "720p", "插入/道具特写"),
    StageModel("video_final", "video", "seedance_2_5_spicy", "ark", "1080p", "仅显式 final"),
    # 声音
    StageModel("tts", "tts", "volc_tts_default", "volc_tts", note="角色配音"),
]}

_runtime: dict[str, dict[str, str]] = {}  # admin overrides (ledger team persists these)


def model_for(stage: str) -> StageModel:
    base = STAGES[stage]
    over = dict(_runtime.get(stage, {}))
    env = os.environ.get(f"MODEL_{stage.upper()}")
    if env:
        over["model"] = env
    env_p = os.environ.get(f"PROVIDER_{stage.upper()}")
    if env_p:
        over["provider"] = env_p
    return replace(base, **over) if over else base


def video_stage_for(shot_kind: str, final: bool = False, draft: bool = False) -> str:
    if draft:
        return "video_draft"
    if final:
        return "video_final"
    return f"video_{shot_kind}" if f"video_{shot_kind}" in STAGES else "video_dialogue"


def set_override(stage: str, **fields: str) -> None:
    if stage not in STAGES:
        raise KeyError(stage)
    _runtime[stage] = {k: v for k, v in fields.items() if v}
