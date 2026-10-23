"""Every LLM call takes its model from gateway.routing.model_for(stage); openai_compat stages go to an
OpenAI-compatible /v1/chat/completions. Only HTTP transports are stubbed."""
import json

import httpx
import pytest

from src.domains.gateway.routing import model_for
from src.domains.script.script_engine import CachedDirectorLLM, EpisodeScript, OpenAICompatLLM
from tests.test_script_engine import STORYBOARD, claude_response, make_claude

SCRIPT = {"episode_title": "信", "script_text": "场1"}


def _claude(seen: list[dict], out: dict):
    def handler(request):
        seen.append(json.loads(request.content))
        return claude_response(json.dumps(out, ensure_ascii=False))
    return make_claude(handler)


async def test_each_stage_uses_its_routed_model(monkeypatch):
    seen: list[dict] = []
    llm = CachedDirectorLLM(client=_claude(seen, SCRIPT))
    await llm.structured(EpisodeScript, "R", {}, "x", stage="plan")
    await llm.structured(EpisodeScript, "R", {}, "x", stage="script")
    expected = [model_for("plan").model, model_for("script").model, "claude-haiku-5"]
    monkeypatch.setenv("MODEL_SCRIPT", "claude-haiku-5")
    await llm.structured(EpisodeScript, "R", {}, "x", stage="script")
    assert [b["model"] for b in seen] == expected
    assert [c["stage"] for c in llm.calls] == ["plan", "script", "script"]
    assert llm.calls[0]["model"] == "claude-opus-5-5"
    assert llm.calls[0]["usage"]["cache_read_input_tokens"] == 9000


async def test_storyboard_defaults_to_storyboard_stage():
    seen: list[dict] = []
    llm = CachedDirectorLLM(client=_claude(seen, STORYBOARD))
    await llm.compile_episode_storyboard("R", {}, "x")
    assert seen[0]["model"] == model_for("storyboard").model and llm.calls[0]["stage"] == "storyboard"


async def test_openai_compat_stage_goes_to_chat_completions(monkeypatch):
    monkeypatch.setenv("PROVIDER_SUMMARIZE", "openai_compat")
    monkeypatch.setenv("MODEL_SUMMARIZE", "deepseek-chat")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(SCRIPT)}}],
                                         "usage": {"prompt_tokens": 120, "completion_tokens": 30}})

    def claude_handler(request):
        raise AssertionError("Claude must not be called for an openai_compat stage")

    compat = OpenAICompatLLM("https://cheap.example/", api_key="k",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    llm = CachedDirectorLLM(client=make_claude(claude_handler), compat=compat)
    out = await llm.structured(EpisodeScript, "R", {"a": 1}, "x", stage="summarize")
    assert out.script_text == "场1"
    assert seen["url"] == "https://cheap.example/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "deepseek-chat"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert llm.calls == [{"stage": "summarize", "model": "deepseek-chat", "provider": "openai_compat",
                          "usage": {"input_tokens": 120, "output_tokens": 30}}]


async def test_openai_compat_without_endpoint_is_blocked(monkeypatch):
    monkeypatch.setenv("PROVIDER_SUMMARIZE", "openai_compat")
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)
    llm = CachedDirectorLLM(client=make_claude(lambda r: claude_response("{}")))
    with pytest.raises(RuntimeError, match="OPENAI_COMPAT_BASE_URL"):
        await llm.structured(EpisodeScript, "R", {}, "x", stage="summarize")


def test_no_hardcoded_model_defaults():
    from src.domains.script import script_engine
    assert not hasattr(script_engine, "DEFAULT_MODEL") and not hasattr(script_engine, "LIGHT_MODEL")
