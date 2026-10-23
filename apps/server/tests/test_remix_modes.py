"""Remix modes -> one structured SeriesPlan call on model_for('remix_rewrite'). Only Claude's transport is stubbed."""
import json

import pytest
from pydantic import ValidationError

from src.domains.gateway.routing import model_for
from src.domains.gateway.safety import ContentBlocked
from src.domains.remix import modes
from src.domains.script.script_engine import CachedDirectorLLM
from tests.test_script_engine import claude_response, make_claude

SOURCE = "第1集 原片\n镜头1 画面：雨夜街头 台词：你凭什么？\n\n第2集 原片\n镜头1 画面：天台 台词：原来是你"
RHYTHM = {"episodes": [{"episode_number": 1, "shot_count": 7, "total_sec": 24.0, "avg_shot_sec": 3.4,
                        "cuts_per_min": 17.5, "opening_hook_in_3s": True, "hooks": []},
                       {"episode_number": 2, "shot_count": 2, "total_sec": 10.0, "avg_shot_sec": 5.0,
                        "cuts_per_min": 12.0, "opening_hook_in_3s": True, "hooks": []}],
          "avg_shot_sec": 3.78, "avg_shots_per_episode": 4.5, "avg_episode_sec": 17.0}
PLAN = {"title": "雨夜新篇", "logline": "l", "characters": [
    {"name": "沈遥", "role_tag": "主角", "visual_prompt": "三十岁，齐肩卷发，深灰西装", "personality": "冷静"}],
    "props": [], "scenes": [{"name": "天台", "visual_prompt": "赛博霓虹天台"}],
    "episodes": [{"episode_number": 3, "title": "a", "synopsis": "s", "source_segments": [1]},
                 {"episode_number": 7, "title": "b", "synopsis": "s", "source_segments": [2]}]}


def llm_with(seen: list, plan: dict = PLAN) -> CachedDirectorLLM:
    def handler(request):
        seen.append(json.loads(request.content))
        return claude_response(json.dumps(plan, ensure_ascii=False))
    return CachedDirectorLLM(client=make_claude(handler))


def test_spec_validation():
    with pytest.raises(ValidationError):
        modes.RemixSpec(modes=[])
    with pytest.raises(ValidationError, match="style"):
        modes.RemixSpec(modes=["restyle"])
    with pytest.raises(ValidationError, match="plot_direction"):
        modes.RemixSpec(modes=["replot"])
    with pytest.raises(ValidationError, match="spinoff_focus"):
        modes.RemixSpec(modes=["spinoff"])
    with pytest.raises(ValidationError, match="condense_ratio"):
        modes.RemixSpec(modes=["condense"])
    with pytest.raises(ValidationError, match="sequel"):
        modes.RemixSpec(modes=["sequel", "condense"], condense_ratio=0.5)
    with pytest.raises(ValidationError, match="duplicate"):
        modes.RemixSpec(modes=["recast", "recast"])
    ok = modes.RemixSpec(modes=["recast", "restyle", "replot"], recast={"林晚": "改成男性律师"}, style="赛博朋克",
                         plot_direction="反派洗白")
    assert ok.modes == ["recast", "restyle", "replot"]


def test_prompt_has_every_combined_mode_and_rhythm():
    spec = modes.RemixSpec(modes=["recast", "restyle", "condense"], recast={"林晚": "改成男性律师"}, style="赛博朋克",
                           condense_ratio=0.5)
    p = modes.rewrite_prompt(spec, SOURCE, RHYTHM, external=True)
    assert "林晚 → 改成男性律师" in p and "赛博朋克" in p and "共 1 集" in p
    assert "【片段 1】" in p and "【片段 2】" in p
    assert "平均镜长 3.78 秒" in p
    assert "重新设计" in p  # external source: never reuse the original performers' looks
    own = modes.rewrite_prompt(modes.RemixSpec(modes=["sequel"], episode_count=3), SOURCE, {}, external=False,
                               parent_episodes=[{"episode_number": 1, "title": "雨", "synopsis": "雨夜相遇"}])
    assert "续写" in own and "雨夜相遇" in own and "共 3 集" in own and "重新设计" not in own


async def test_rewrite_uses_remix_rewrite_model_and_renumbers():
    seen: list = []
    spec = modes.RemixSpec(modes=["restyle", "replot"], style="民国", plot_direction="改成复仇线")
    plan = await modes.rewrite(llm_with(seen), spec, SOURCE, RHYTHM, bible={"rhythm": RHYTHM},
                               content_mode="standard_sfw", external=True)
    body = seen[0]
    assert body["model"] == model_for("remix_rewrite").model
    assert "episodes" in json.dumps(body["output_config"]["format"]["schema"])
    assert [b["cache_control"]["ttl"] for b in body["system"]] == ["1h", "1h"]
    assert "民国" in body["messages"][0]["content"]
    assert [e.episode_number for e in plan.episodes] == [1, 2]


async def test_rewrite_model_follows_routing_override(monkeypatch):
    monkeypatch.setenv("MODEL_REMIX_REWRITE", "claude-opus-5-5")
    seen: list = []
    await modes.rewrite(llm_with(seen), modes.RemixSpec(modes=["sequel"]), SOURCE, {}, {}, "standard_sfw", False)
    assert seen[0]["model"] == "claude-opus-5-5"


async def test_unsafe_direction_blocked_before_any_call():
    seen: list = []
    spec = modes.RemixSpec(modes=["replot"], plot_direction="改成高中生之间的床戏")
    with pytest.raises(ContentBlocked):
        await modes.rewrite(llm_with(seen), spec, SOURCE, {}, {}, "standard_sfw", True)
    assert seen == []


async def test_unsafe_plan_output_blocked():
    bad = {**PLAN, "characters": [{"name": "x", "role_tag": "主角", "visual_prompt": "16岁少女，性感", "personality": "p"}]}
    with pytest.raises(ContentBlocked):
        await modes.rewrite(llm_with([], bad), modes.RemixSpec(modes=["sequel"]), SOURCE, {}, {}, "standard_sfw", False)
