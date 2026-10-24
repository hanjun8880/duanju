"""Cost controls must affect the actual request, not just the label in the UI."""
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.domains.gateway import routing
from src.domains.script import api as script_api
from src.domains.script.script_engine import EpisodeScript, OpenAICompatLLM
from src.main import app


@pytest.mark.parametrize(("base", "endpoint"), [
    ("https://api.example", "https://api.example/v1/chat/completions"),
    ("https://api.example/v1/", "https://api.example/v1/chat/completions"),
    ("https://api.example/api/paas/v4", "https://api.example/api/paas/v4/chat/completions"),
    ("https://api.example/api/v3", "https://api.example/api/v3/chat/completions"),
])
async def test_compat_uses_vendor_api_prefix(base, endpoint):
    def handler(request):
        assert str(request.url) == endpoint
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"episode_title": "试片", "script_text": "场1"})}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAICompatLLM(base, client=client)
        await llm.structured(EpisodeScript, "R", {}, "x")


async def test_project_tiers_roundtrip_validation_and_effective_route(monkeypatch):
    monkeypatch.setattr(routing, "_runtime", {})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        pid = (await client.post("/api/v1/projects", json={"title": "节省成本"})).json()["id"]
        path = f"/api/v1/projects/{pid}/quality-tiers"
        initial = await client.get(path)
        assert initial.status_code == 200 and initial.json()["tiers"] == {}
        saved = {"script": "draft", "plan": "standard"}
        assert (await client.put(path, json={"tiers": saved})).status_code == 200
        assert (await client.get(path)).json()["tiers"] == saved
        for bad in ({"script": "typo"}, {"video_final": "draft"}, {"storyboard": "overseas"}):
            assert (await client.put(path, json={"tiers": bad})).status_code == 422
            assert (await client.get(path)).json()["tiers"] == saved
        monkeypatch.setenv("MODEL_SCRIPT", "deployment-model")
        options = (await client.get("/api/v1/stage-tiers")).json()
        assert options["script"]["draft"]["effective_model"] == "deployment-model"


def test_domestic_route_does_not_require_claude_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(routing, "_runtime", {})
    project = script_api.Project(content_mode="standard_sfw", series_bible_json={
        "_quality_tiers": {"script": "draft"}})
    script_api._need_llm(project, "script")
    with pytest.raises(script_api.HTTPException, match="ANTHROPIC_API_KEY"):
        script_api._need_llm(project, "script", "standard")


async def test_economy_pipeline_without_claude_credentials(monkeypatch):
    """Exercise saved choices through domain REST endpoints; only vendor HTTP is replaced."""
    from src.domains.script import script_engine
    from tests.test_script_engine import STORYBOARD
    from tests.test_series import PLAN, SCRIPT

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "test")
    monkeypatch.setenv("ARK_API_KEY", "test")
    monkeypatch.setattr(routing, "_runtime", {})
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body["model"])
        properties = body["response_format"]["json_schema"]["schema"]["properties"]
        result = PLAN if "episodes" in properties else SCRIPT if "script_text" in properties else STORYBOARD
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}],
                                        "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    clients = []

    def client_factory(*args, **kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(script_engine, "httpx", type("HTTP", (), {"AsyncClient": staticmethod(client_factory)}))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            pid = (await client.post("/api/v1/projects", json={"title": "试写", "source_novel_text": "雾港来信"})).json()["id"]
            path = f"/api/v1/projects/{pid}"
            choices = {stage: "draft" for stage in ("plan", "script", "storyboard", "reformat")}
            assert (await client.put(f"{path}/quality-tiers", json={"tiers": choices})).status_code == 200
            for suffix, body in (("plan", {}), ("episodes/1/script", None), ("episodes/1/storyboard", None)):
                response = await client.post(f"{path}/{suffix}", json=body)
                assert response.status_code == 201, response.text
            assert seen == [routing.model_for(stage, "draft").model for stage in ("plan", "script", "storyboard")]
    finally:
        for client in clients:
            await client.aclose()
