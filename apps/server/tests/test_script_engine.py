"""Exercises the real Anthropic SDK request serialisation; only the HTTP transport is stubbed."""
import json

import httpx
import httpx2
import pytest
from anthropic import AsyncAnthropic

from src.domains.script.script_engine import (
    CachedDirectorLLM,
    EpisodeStoryboardOutput,
    LLMRefused,
    UncensoredLLM,
    build_system,
)

STORYBOARD = {"episode_title": "E1", "hook_summary": "h", "shots": [{
    "shot_order": 1, "shot_scale": "MS", "duration": 15, "camera_motion": "dolly in",
    "five_part_visual_prompt": "1..5", "dialogue": "你好", "character_names": ["林"], "prop_names": [],
    "scene_name": "天台", "suggested_pose_template": "window silhouette"}]}


def claude_response(text: str, stop_reason: str = "end_turn") -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10, "cache_read_input_tokens": 9000}}


def make_claude(handler):
    # anthropic>=2 ships on httpx2; stub only the transport
    return AsyncAnthropic(api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
                          max_retries=0)


def test_system_prefix_is_byte_stable_and_cached():
    a = build_system("RULES", {"b": 1, "a": {"y": 2, "x": 1}})
    b = build_system("RULES", {"a": {"x": 1, "y": 2}, "b": 1})
    assert json.dumps(a) == json.dumps(b)
    assert all(blk["cache_control"] == {"type": "ephemeral", "ttl": "1h"} for blk in a)
    assert a[1]["text"].startswith("SERIES_BIBLE_LOCKED_ASSETS:\n")


async def test_storyboard_request_shape_and_parse():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=claude_response(json.dumps(STORYBOARD, ensure_ascii=False)))

    llm = CachedDirectorLLM(client=make_claude(handler))
    out = await llm.compile_episode_storyboard("RULES", {"characters": []}, "第一章……")
    assert isinstance(out, EpisodeStoryboardOutput) and out.shots[0].duration == 15
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["messages"][-1]["role"] == "user"  # no assistant prefill
    assert [b["cache_control"]["ttl"] for b in body["system"]] == ["1h", "1h"]
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and "shots" in json.dumps(fmt["schema"])
    assert body["output_config"]["effort"] == "medium"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    assert "thinking" not in body and "temperature" not in body
    assert llm.last_usage["cache_read_input_tokens"] == 9000


async def test_refusal_raises():
    llm = CachedDirectorLLM(client=make_claude(lambda r: httpx2.Response(200, json=claude_response("", "refusal"))))
    with pytest.raises(LLMRefused):
        await llm.compile_episode_storyboard("R", {}, "x")


async def test_uncensored_mode_routes_to_private_llm_not_claude():
    def claude_handler(request):  # must never be called
        raise AssertionError("Claude called in uncensored mode")

    seen = {}

    def private_handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(STORYBOARD)}}]})

    private = UncensoredLLM(base_url="http://llm.local", model="llama-3.1-70b",
                            client=httpx.AsyncClient(transport=httpx.MockTransport(private_handler)))
    llm = CachedDirectorLLM(client=make_claude(claude_handler), uncensored=private)
    out = await llm.compile_episode_storyboard("R", {"k": 1}, "x", content_mode="uncensored_nsfw")
    assert out.episode_title == "E1"
    assert seen["url"] == "http://llm.local/v1/chat/completions"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert "SERIES_BIBLE_LOCKED_ASSETS" in seen["body"]["messages"][0]["content"]


async def test_uncensored_mode_without_private_llm_is_blocked():
    llm = CachedDirectorLLM(client=make_claude(lambda r: httpx2.Response(500)))
    with pytest.raises(RuntimeError, match="UNCENSORED_LLM_BASE_URL"):
        await llm.compile_episode_storyboard("R", {}, "x", content_mode="uncensored_nsfw")
