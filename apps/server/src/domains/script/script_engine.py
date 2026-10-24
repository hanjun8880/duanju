"""Cached structured storyboard compiler (spec 机制一/二).

Every call names its pipeline stage; the model and provider come from gateway.routing.model_for(stage).
provider=anthropic -> Claude as below; provider=openai_compat -> OPENAI_COMPAT_BASE_URL /v1/chat/completions
(cheap models for e.g. summarize). Each call's usage is appended to CachedDirectorLLM.calls for the job ledger.

standard_sfw -> Claude via the official SDK: system rules + SERIES_BIBLE as two byte-stable blocks with a
1h cache breakpoint each, structured output (output_config.format json_schema via messages.parse), no prefill.
uncensored_nsfw -> a private OpenAI-compatible endpoint (vLLM / llama.cpp / Ollama style), never Claude.
"""
import json
import os
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, Field

from src.domains.gateway.routing import model_for

FALLBACK_BETA = "server-side-fallback-2026-07-01"
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ShotSchema(BaseModel):
    shot_order: int
    shot_scale: Literal["ECU", "CU", "MCU", "MS", "WS", "EWS"]
    shot_kind: Literal["dialogue", "action", "establishing", "insert"] = Field(
        default="dialogue", description="dialogue 对白/人物；action 动作；establishing 空镜/环境；insert 插入/道具特写")
    duration: int = Field(ge=4, le=10, description="秒；4-8 秒为主，最长 10 秒")
    camera_motion: str
    five_part_visual_prompt: str = Field(
        description="Continuous arc: 1.Opening frame 2.Camera move 3.Subject action 4.Environment shift 5.Final frame"
    )
    dialogue: str
    speaker: str = Field(default="", description="说这句台词的角色名（SERIES_BIBLE 中的名称）；无台词为空")
    continuous_from_prev: bool = Field(
        default=False, description="与上一镜是同一连续动作/同一机位的延续（用上一镜尾帧接续）时为 true")
    character_names: list[str]
    prop_names: list[str]
    scene_name: str
    suggested_pose_template: str


class EpisodeStoryboardOutput(BaseModel):
    episode_title: str
    hook_summary: str
    shots: list[ShotSchema]


class BibleCharacter(BaseModel):
    name: str
    role_tag: str = Field(description="主角/反派/配角 等")
    visual_prompt: str = Field(description="可直接用于出定妆图的外貌、年龄段（成年）、发型、服装、气质描述")
    personality: str


class BibleItem(BaseModel):
    name: str
    visual_prompt: str = Field(description="可直接用于出设定图的外观、材质、光影描述")


class EpisodeOutline(BaseModel):
    episode_number: int
    title: str
    synopsis: str = Field(description="本集剧情梗概，含开场钩子与结尾悬念")
    source_segments: list[int] = Field(description="本集改编自哪些【片段 i】编号；原创想法时为空")


class SeriesPlan(BaseModel):
    title: str
    logline: str
    characters: list[BibleCharacter]
    props: list[BibleItem]
    scenes: list[BibleItem]
    episodes: list[EpisodeOutline]


class SegmentDigest(BaseModel):
    segment: int = Field(description="【片段 i】的编号 i")
    summary: str = Field(description="200 字内剧情摘要：关键情节、转折、伏笔")
    characters: list[str] = Field(description="出场人物（统一用全名）")
    scenes: list[str]
    props: list[str]


class DigestBatch(BaseModel):
    digests: list[SegmentDigest]


class SeriesSetting(BaseModel):
    title: str
    logline: str
    episode_count: int = Field(description="建议总集数")
    characters: list[BibleCharacter]
    props: list[BibleItem]
    scenes: list[BibleItem]


class EpisodeOutlines(BaseModel):
    episodes: list[EpisodeOutline]


class EpisodeScript(BaseModel):
    episode_title: str
    script_text: str = Field(description="分场剧本：场号、日/夜、内/外、地点；动作描写；角色名：台词")


class KeyframeNote(BaseModel):
    shot_order: int
    description: str = Field(description="画面里发生了什么：人物、动作、场景、光线、镜头景别与运动")


class KeyframeNotes(BaseModel):
    shots: list[KeyframeNote]


# Built once: a stable schema keeps the compiled-grammar cache warm (spec 机制二).
STORYBOARD_SCHEMA = transform_schema(EpisodeStoryboardOutput)
_SCHEMAS: dict[type[BaseModel], dict[str, Any]] = {EpisodeStoryboardOutput: STORYBOARD_SCHEMA}
T = TypeVar("T", bound=BaseModel)


class LLMRefused(Exception):
    pass


def bible_text(series_bible: dict[str, Any]) -> str:
    # sort_keys: dict ordering must never change the cached prefix bytes; "_" keys are bookkeeping
    # (_cache digests, _progress), never part of the prompt
    locked = {k: v for k, v in series_bible.items() if not k.startswith("_")}
    return "SERIES_BIBLE_LOCKED_ASSETS:\n" + json.dumps(locked, ensure_ascii=False, sort_keys=True)


def build_system(system_rules: str, series_bible: dict[str, Any], ttl: Literal["5m", "1h"] = "1h") -> list[Any]:
    cache = {"type": "ephemeral", "ttl": ttl}
    return [{"type": "text", "text": system_rules, "cache_control": cache},
            {"type": "text", "text": bible_text(series_bible), "cache_control": cache}]


def user_prompt(episode_raw_text: str) -> str:
    return f"将以下剧本拆解为连续剧分镜表（每镜 4-8 秒为主，最长 10 秒）：\n{episode_raw_text}"


# 国产文本模型默认走的 OpenAI-compatible 网关：专家选型 2026-10-23 口头核实价格，接口文档未核实（NOT_TESTED）。
# base_url 可用 <PROVIDER>_BASE_URL 覆盖；api_key 环境变量名见下。doubao 复用方舟账号自己的 ARK_API_KEY。
VENDOR_BASE_URL = {
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "moonshot": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",  # Grok：偏海外题材剧本的可选项，默认分工不用，按项目/档位手动选
}
VENDOR_API_KEY_ENV = {
    "doubao": "ARK_API_KEY", "zhipu": "ZHIPU_API_KEY", "moonshot": "MOONSHOT_API_KEY",
    "qwen": "QWEN_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "xai": "XAI_API_KEY",
}


class OpenAICompatLLM:
    """Any OpenAI-compatible /v1/chat/completions endpoint (NOT_TESTED against a live server)."""

    def __init__(self, base_url: str, model: str = "", client: httpx.AsyncClient | None = None,
                 api_key: str | None = None):
        self.base_url, self.model = base_url.rstrip("/"), model
        self.client = client or httpx.AsyncClient(timeout=600)
        self.api_key = api_key
        self.last_usage: dict[str, Any] = {}

    @classmethod
    def from_env(cls) -> "OpenAICompatLLM | None":
        url = os.environ.get("OPENAI_COMPAT_BASE_URL")
        return cls(url, api_key=os.environ.get("OPENAI_COMPAT_API_KEY")) if url else None

    @classmethod
    def for_vendor(cls, provider: str) -> "OpenAICompatLLM | None":
        """A named vendor (doubao/zhipu/moonshot/qwen/deepseek) with its own base URL + API key env var,
        overridable per vendor with <PROVIDER>_BASE_URL. Missing key -> None (caller falls back/errors)."""
        base = os.environ.get(f"{provider.upper()}_BASE_URL", VENDOR_BASE_URL.get(provider))
        key_env = VENDOR_API_KEY_ENV.get(provider)
        key = os.environ.get(key_env) if key_env else None
        return cls(base, api_key=key) if base and key else None

    async def structured(self, out: type[T], system_rules: str, series_bible: dict[str, Any], prompt: str,
                         model: str | None = None) -> T:
        body = {"model": model or self.model, "messages": [
            {"role": "system", "content": f"{system_rules}\n\n{bible_text(series_bible)}"},
            {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": out.__name__, "schema": out.model_json_schema()}}}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        # Named vendors already include their API prefix (/v1, /api/v3, /api/paas/v4).
        # Preserve root-only URLs used by existing self-hosted OpenAI-compatible deployments.
        prefix = self.base_url if urlsplit(self.base_url).path else f"{self.base_url}/v1"
        r = await self.client.post(f"{prefix}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
        u = data.get("usage") or {}
        self.last_usage = {"input_tokens": int(u.get("prompt_tokens", 0)),
                           "output_tokens": int(u.get("completion_tokens", 0))}
        return out.model_validate_json(data["choices"][0]["message"]["content"])


class UncensoredLLM(OpenAICompatLLM):
    """Private model for uncensored_nsfw (vLLM / llama.cpp / Ollama style), never Claude."""

    @classmethod
    def from_env(cls) -> "UncensoredLLM | None":
        url = os.environ.get("UNCENSORED_LLM_BASE_URL")
        return cls(url, os.environ.get("UNCENSORED_LLM_MODEL", "llama-3.1-70b-instruct"),
                   api_key=os.environ.get("UNCENSORED_LLM_API_KEY")) if url else None


class CachedDirectorLLM:
    def __init__(self, client: AsyncAnthropic | None = None, model: str | None = None,
                 uncensored: UncensoredLLM | None = None, compat: OpenAICompatLLM | None = None):
        self._client = client
        self.model = model  # explicit pin for every call; normally None -> model_for(stage)
        self.uncensored = uncensored if uncensored is not None else UncensoredLLM.from_env()
        self.compat = compat
        self.last_usage: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []  # one entry per LLM call: stage, model, provider, usage

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic()
        return self._client

    def _record(self, stage: str, model: str, provider: str, usage: dict[str, Any]) -> None:
        self.last_usage = usage
        self.calls.append({"stage": stage, "model": model, "provider": provider, "usage": usage})

    async def structured(self, out: type[T], system_rules: str, series_bible: dict[str, Any],
                         prompt: str | list[dict[str, Any]], content_mode: str = "standard_sfw",
                         effort: Effort = "medium", model: str | None = None, max_tokens: int = 32000,
                         stage: str = "storyboard", tier: str | None = None) -> T:
        if content_mode == "uncensored_nsfw":
            if self.uncensored is None:
                raise RuntimeError("uncensored_nsfw needs a private LLM: set UNCENSORED_LLM_BASE_URL")
            if not isinstance(prompt, str):
                raise RuntimeError("the private LLM path is text-only")
            res = await self.uncensored.structured(out, system_rules, series_bible, prompt)
            self._record(stage, self.uncensored.model, "uncensored", self.uncensored.last_usage)
            return res
        routed = model_for(stage, tier)
        use_model = model or self.model or routed.model
        if routed.provider != "anthropic":
            compat = self.compat or OpenAICompatLLM.from_env() if routed.provider == "openai_compat" \
                else OpenAICompatLLM.for_vendor(routed.provider)
            if compat is None:
                env_hint = VENDOR_API_KEY_ENV.get(routed.provider, "OPENAI_COMPAT_BASE_URL/OPENAI_COMPAT_API_KEY")
                raise RuntimeError(f"stage {stage} routes to {routed.provider}: set {env_hint}")
            if not isinstance(prompt, str):
                raise RuntimeError(f"the {routed.provider} path is text-only")
            res = await compat.structured(out, system_rules, series_bible, prompt, model=use_model)
            self._record(stage, use_model, routed.provider, compat.last_usage)
            return res
        schema = _SCHEMAS.setdefault(out, transform_schema(out))
        messages: list[Any] = [{"role": "user", "content": prompt}]
        # Opus 5.5: thinking is always adaptive (no budget_tokens); effort is the cost knob.
        # Streaming: long novels and many episodes can run past the non-streaming request timeout.
        async with self.client.beta.messages.stream(
            model=use_model,
            max_tokens=max_tokens,
            system=build_system(system_rules, series_bible),
            messages=messages,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=[FALLBACK_BETA],
            fallbacks="default",  # server-side model fallback when a safety classifier declines
        ) as stream:
            response = await stream.get_final_message()
        # recorded before the stop_reason checks: a truncated or refused answer still cost tokens
        self._record(stage, use_model, "anthropic", response.usage.model_dump(exclude_none=True))
        # check stop_reason before touching content: a refusal carries no JSON
        if response.stop_reason == "refusal":
            raise LLMRefused(str(getattr(response, "stop_details", None) or "refusal"))
        if response.stop_reason == "max_tokens":
            raise ValueError("output truncated at max_tokens; split the source text")
        text = "".join(b.text for b in response.content if b.type == "text")
        return out.model_validate_json(text)

    async def compile_episode_storyboard(
        self, system_rules: str, series_bible_json: dict[str, Any], episode_raw_text: str,
        content_mode: str = "standard_sfw", effort: Effort = "medium", tier: str | None = None,
    ) -> EpisodeStoryboardOutput:
        return await self.structured(EpisodeStoryboardOutput, system_rules, series_bible_json,
                                     user_prompt(episode_raw_text), content_mode, effort, stage="storyboard",
                                     tier=tier)
