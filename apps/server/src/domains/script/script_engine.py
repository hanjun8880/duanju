"""Cached structured storyboard compiler (spec 机制一/二).

standard_sfw -> Claude via the official SDK: system rules + SERIES_BIBLE as two byte-stable blocks with a
1h cache breakpoint each, structured output (output_config.format json_schema via messages.parse), no prefill.
uncensored_nsfw -> a private OpenAI-compatible endpoint (vLLM / llama.cpp / Ollama style), never Claude.
"""
import json
import os
from typing import Any, Literal

import httpx
from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, Field

DEFAULT_MODEL = os.environ.get("DIRECTOR_LLM_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
Effort = Literal["low", "medium", "high", "xhigh", "max"]
# Built once: a stable schema keeps the compiled-grammar cache warm (spec 机制二).


class ShotSchema(BaseModel):
    shot_order: int
    shot_scale: Literal["ECU", "CU", "MCU", "MS", "WS", "EWS"]
    duration: int = Field(ge=4, le=30)
    camera_motion: str
    five_part_visual_prompt: str = Field(
        description="Continuous arc: 1.Opening frame 2.Camera move 3.Subject action 4.Environment shift 5.Final frame"
    )
    dialogue: str
    character_names: list[str]
    prop_names: list[str]
    scene_name: str
    suggested_pose_template: str


class EpisodeStoryboardOutput(BaseModel):
    episode_title: str
    hook_summary: str
    shots: list[ShotSchema]


STORYBOARD_SCHEMA = transform_schema(EpisodeStoryboardOutput)


class LLMRefused(Exception):
    pass


def bible_text(series_bible: dict[str, Any]) -> str:
    # sort_keys: dict ordering must never change the cached prefix bytes
    return "SERIES_BIBLE_LOCKED_ASSETS:\n" + json.dumps(series_bible, ensure_ascii=False, sort_keys=True)


def build_system(system_rules: str, series_bible: dict[str, Any], ttl: Literal["5m", "1h"] = "1h") -> list[Any]:
    cache = {"type": "ephemeral", "ttl": ttl}
    return [{"type": "text", "text": system_rules, "cache_control": cache},
            {"type": "text", "text": bible_text(series_bible), "cache_control": cache}]


def user_prompt(episode_raw_text: str) -> str:
    return f"将以下小说章节/二创大纲拆解为连续剧分镜表（优先合并为15-30秒长镜头）：\n{episode_raw_text}"


class UncensoredLLM:
    """Private model behind an OpenAI-compatible /v1/chat/completions (NOT_TESTED against a live server)."""

    def __init__(self, base_url: str, model: str, client: httpx.AsyncClient | None = None,
                 api_key: str | None = None):
        self.base_url, self.model = base_url.rstrip("/"), model
        self.client = client or httpx.AsyncClient(timeout=600)
        self.api_key = api_key

    @classmethod
    def from_env(cls) -> "UncensoredLLM | None":
        url = os.environ.get("UNCENSORED_LLM_BASE_URL")
        return cls(url, os.environ.get("UNCENSORED_LLM_MODEL", "llama-3.1-70b-instruct"),
                   api_key=os.environ.get("UNCENSORED_LLM_API_KEY")) if url else None

    async def storyboard(self, system_rules: str, series_bible: dict[str, Any], text: str) -> EpisodeStoryboardOutput:
        body = {"model": self.model, "messages": [
            {"role": "system", "content": f"{system_rules}\n\n{bible_text(series_bible)}"},
            {"role": "user", "content": user_prompt(text)}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "EpisodeStoryboardOutput", "schema": EpisodeStoryboardOutput.model_json_schema()}}}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        r = await self.client.post(f"{self.base_url}/v1/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        return EpisodeStoryboardOutput.model_validate_json(r.json()["choices"][0]["message"]["content"])


class CachedDirectorLLM:
    def __init__(self, client: AsyncAnthropic | None = None, model: str = DEFAULT_MODEL,
                 uncensored: UncensoredLLM | None = None):
        self.client = client or AsyncAnthropic()
        self.model = model
        self.uncensored = uncensored if uncensored is not None else UncensoredLLM.from_env()
        self.last_usage: dict[str, Any] = {}

    async def compile_episode_storyboard(
        self, system_rules: str, series_bible_json: dict[str, Any], episode_raw_text: str,
        content_mode: str = "standard_sfw", effort: Effort = "medium",
    ) -> EpisodeStoryboardOutput:
        if content_mode == "uncensored_nsfw":
            if self.uncensored is None:
                raise RuntimeError("uncensored_nsfw needs a private LLM: set UNCENSORED_LLM_BASE_URL")
            return await self.uncensored.storyboard(system_rules, series_bible_json, episode_raw_text)
        # Opus 5.5: thinking is always adaptive (no budget_tokens); effort is the cost knob.
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=build_system(system_rules, series_bible_json),
            messages=[{"role": "user", "content": user_prompt(episode_raw_text)}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": STORYBOARD_SCHEMA}},
            betas=[FALLBACK_BETA],
            fallbacks="default",  # server-side model fallback when a safety classifier declines
        )
        self.last_usage = response.usage.model_dump()
        # check stop_reason before touching content: a refusal carries no JSON
        if response.stop_reason == "refusal":
            raise LLMRefused(str(response.stop_details))
        if response.stop_reason == "max_tokens":
            raise ValueError("storyboard truncated at max_tokens; split the episode text")
        text = "".join(b.text for b in response.content if b.type == "text")
        return EpisodeStoryboardOutput.model_validate_json(text)
