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


async def test_named_vendor_stage_goes_to_its_own_base_url(monkeypatch):
    """A stage routed to a named vendor (e.g. doubao/zhipu/moonshot) hits that vendor's own base URL with its
    own API key env var, not the generic OPENAI_COMPAT_* pair."""
    monkeypatch.setenv("PROVIDER_SUMMARIZE", "zhipu")
    monkeypatch.setenv("MODEL_SUMMARIZE", "glm-5.3")
    monkeypatch.setenv("ZHIPU_API_KEY", "zk")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(SCRIPT)}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    from src.domains.script import script_engine
    from src.domains.script.script_engine import VENDOR_BASE_URL
    monkeypatch.setattr(script_engine, "httpx",
                       type("M", (), {"AsyncClient": lambda *a, **kw: httpx.AsyncClient(
                           transport=httpx.MockTransport(handler))})())
    llm = CachedDirectorLLM(client=make_claude(lambda r: (_ for _ in ()).throw(AssertionError("no Claude"))))
    out = await llm.structured(EpisodeScript, "R", {}, "x", stage="summarize")
    assert out.script_text == "场1"
    assert seen["url"] == VENDOR_BASE_URL["zhipu"] + "/v1/chat/completions" and seen["auth"] == "Bearer zk"
    assert llm.calls[0]["provider"] == "zhipu" and llm.calls[0]["model"] == "glm-5.3"


async def test_named_vendor_without_key_is_blocked(monkeypatch):
    monkeypatch.setenv("PROVIDER_SUMMARIZE", "zhipu")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    llm = CachedDirectorLLM(client=make_claude(lambda r: claude_response("{}")))
    with pytest.raises(RuntimeError, match="ZHIPU_API_KEY"):
        await llm.structured(EpisodeScript, "R", {}, "x", stage="summarize")


async def test_openai_compat_without_endpoint_is_blocked(monkeypatch):
    monkeypatch.setenv("PROVIDER_SUMMARIZE", "openai_compat")
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)
    llm = CachedDirectorLLM(client=make_claude(lambda r: claude_response("{}")))
    with pytest.raises(RuntimeError, match="OPENAI_COMPAT_BASE_URL"):
        await llm.structured(EpisodeScript, "R", {}, "x", stage="summarize")


def test_no_hardcoded_model_defaults():
    from src.domains.script import script_engine
    assert not hasattr(script_engine, "DEFAULT_MODEL") and not hasattr(script_engine, "LIGHT_MODEL")


def test_model_for_tier_precedence(monkeypatch):
    """tier picks a QUALITY_TIERS override; admin _runtime override wins over tier; env wins over everything."""
    from src.domains.gateway import routing

    monkeypatch.setattr(routing, "_runtime", {})
    base = routing.model_for("script")
    assert (base.model, base.provider) == ("claude-sonnet-5-5", "anthropic")

    draft = routing.model_for("script", tier="draft")
    assert (draft.model, draft.provider) == ("doubao-seed-2-1-lite-260915", "doubao")

    assert routing.model_for("script", tier="standard").model == base.model

    routing.set_override("script", model="kimi-k3", provider="moonshot")
    try:
        overridden = routing.model_for("script", tier="draft")
        assert (overridden.model, overridden.provider) == ("kimi-k3", "moonshot")

        monkeypatch.setenv("MODEL_SCRIPT", "glm-5.3")
        monkeypatch.setenv("PROVIDER_SCRIPT", "zhipu")
        env_wins = routing.model_for("script", tier="draft")
        assert (env_wins.model, env_wins.provider) == ("glm-5.3", "zhipu")
    finally:
        routing._runtime.pop("script", None)


def test_overseas_tier_routes_script_to_grok():
    from src.domains.gateway import routing
    from src.domains.script.script_engine import VENDOR_API_KEY_ENV

    overseas = routing.model_for("script", tier="overseas")
    assert (overseas.model, overseas.provider) == ("grok-4", "xai")
    assert VENDOR_API_KEY_ENV["xai"] == "XAI_API_KEY"


def test_tiers_for_includes_standard_and_configured_tiers():
    from src.domains.gateway import routing

    tiers = routing.tiers_for("script")
    assert tiers["standard"]["model"] == "claude-sonnet-5-5"
    assert tiers["draft"]["model"] == "doubao-seed-2-1-lite-260915"
    assert tiers["high"]["model"] == "kimi-k3"

    assert routing.tiers_for("video_final") == {
        "standard": {"model": "seedance_2_5_spicy", "provider": "ark", "price_note": "仅显式 final"}
    }
