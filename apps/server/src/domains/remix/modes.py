"""Remix modes (combinable) -> one structured SeriesPlan call on model_for("remix_rewrite").

recast 换人物 | restyle 换风格 | replot 改剧情 | sequel 续写 | spinoff 番外 | condense 精简剪辑.
The user's directions and the returned character designs both pass safety.check_prompt.
"""
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from src.domains.gateway.routing import model_for
from src.domains.gateway.safety import check_prompt
from src.domains.remix.rhythm import rhythm_brief
from src.domains.script import series
from src.domains.script.script_engine import CachedDirectorLLM, SeriesPlan

Mode = Literal["recast", "restyle", "replot", "sequel", "spinoff", "condense"]
MODE_LABEL: dict[str, str] = {"recast": "换人物", "restyle": "换风格", "replot": "改剧情", "sequel": "续写",
                              "spinoff": "番外", "condense": "精简剪辑"}

REWRITE_RULES = (
    "你是短剧二创总编剧兼制片人。输入是一部已有短剧的逐集素材（原片逆向拆解或自家剧集大纲）和二创要求，"
    "输出一部新的竖屏 AI 短剧连续剧规划。严格按二创要求改编，未要求改变的部分保持原作的人物关系、剧情走向与节奏。"
    "每集 1-3 分钟：开场 3 秒内给出冲突钩子，结尾留悬念。先锁定全剧的人物、道具、场景设定（名称全剧唯一且固定），"
    "再给出分集大纲；episode_number 从 1 起连续编号；source_segments 写明每集依据的【片段 i】编号（新写内容可为空）。"
    "visual_prompt 要具体到可以直接出定妆图：年龄段、脸型发型、服装材质与颜色、标志性配饰、整体气质。"
    "所有角色都是虚构的成年人。"
)
EXTERNAL_RULE = ("素材来自外部原片：所有人物必须重新设计外貌（脸型、发型、服装、气质都与原片演员明显不同），"
                 "不得描述或暗示任何真实演员、明星或真人身份。")


class RemixSpec(BaseModel):
    modes: list[Mode] = Field(min_length=1)
    recast: dict[str, str] = Field(default_factory=dict, description="原角色名 -> 新人物设定；空 = 人物全部重新设计")
    style: str | None = Field(default=None, max_length=300, description="换风格：目标视觉/题材风格")
    plot_direction: str | None = Field(default=None, max_length=500, description="改剧情：剧情改动方向")
    spinoff_focus: str | None = Field(default=None, max_length=200, description="番外：围绕的人物或支线")
    condense_ratio: float | None = Field(default=None, gt=0, lt=1, description="精简剪辑：目标集数占原片比例")
    episode_count: int | None = Field(default=None, ge=1, le=200)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("duplicate mode")
        need = {"restyle": ("style", self.style), "replot": ("plot_direction", self.plot_direction),
                "spinoff": ("spinoff_focus", self.spinoff_focus), "condense": ("condense_ratio", self.condense_ratio)}
        for m, (field, value) in need.items():
            if m in self.modes and (value is None or (isinstance(value, str) and not value.strip())):
                raise ValueError(f"mode {m} needs {field}")
        if "sequel" in self.modes and "condense" in self.modes:
            raise ValueError("sequel (continue) and condense (shorten) cannot be combined")
        return self


def directions_text(spec: RemixSpec) -> str:
    parts = [*(f"{k} {v}" for k, v in spec.recast.items()), spec.style, spec.plot_direction, spec.spinoff_focus]
    return "\n".join(p for p in parts if p)


def _target_episodes(spec: RemixSpec, source_episodes: int) -> int | None:
    if spec.episode_count:
        return spec.episode_count
    if spec.condense_ratio and source_episodes:
        return max(1, round(source_episodes * spec.condense_ratio))
    return None


def rewrite_prompt(spec: RemixSpec, source_text: str, rhythm: dict[str, Any], external: bool,
                   parent_episodes: list[dict[str, Any]] | None = None) -> str:
    segments = series.split_source(source_text) if source_text.strip() else []
    n_src = len(rhythm.get("episodes", [])) or len(parent_episodes or []) or len(segments)
    lines = [f"二创模式：{' + '.join(MODE_LABEL[m] for m in spec.modes)}"]
    if "recast" in spec.modes:
        lines.append("换人物：" + ("；".join(f"{k} → {v}" for k, v in spec.recast.items())
                                if spec.recast else "全部人物重新设计，保留人物关系与戏剧功能"))
    if "restyle" in spec.modes:
        lines.append(f"换风格：整体改为「{spec.style}」，场景、服装、道具、光影随之重设，剧情骨架不变")
    if "replot" in spec.modes:
        lines.append(f"改剧情：{spec.plot_direction}")
    if "sequel" in spec.modes:
        lines.append("续写：从原作结尾继续写新的剧情，延续人物与世界观，不复述已有剧情")
    if "spinoff" in spec.modes:
        lines.append(f"番外：围绕「{spec.spinoff_focus}」写独立番外，与主线不矛盾")
    if "condense" in spec.modes:
        lines.append("精简剪辑：只保留主线与最强冲突，合并或删掉支线与重复情节")
    target = _target_episodes(spec, n_src)
    lines.append(f"共 {target} 集" if target else "集数按内容体量自定")
    if external:
        lines.append(EXTERNAL_RULE)
    brief = rhythm_brief(rhythm) if rhythm else ""
    if brief:
        lines.append(brief)
    if parent_episodes:
        lines.append("原作分集：\n" + "\n".join(f"第{e['episode_number']}集《{e['title']}》{e['synopsis']}"
                                             for e in parent_episodes))
    if segments:
        lines.append("原作素材：\n\n" + "\n\n".join(f"【片段 {i}】\n{s}" for i, s in enumerate(segments, 1)))
    return "\n".join(lines)


def _check_plan(plan: SeriesPlan, content_mode: str) -> None:
    for c in plan.characters:
        check_prompt(f"{c.name} {c.visual_prompt} {c.personality}", content_mode, {"stage": "remix_rewrite"})
    for e in plan.episodes:
        check_prompt(f"{e.title} {e.synopsis}", content_mode, {"stage": "remix_rewrite"})


async def rewrite(llm: CachedDirectorLLM, spec: RemixSpec, source_text: str, rhythm: dict[str, Any],
                  bible: dict[str, Any], content_mode: str, external: bool,
                  parent_episodes: list[dict[str, Any]] | None = None) -> SeriesPlan:
    check_prompt(directions_text(spec), content_mode, {"stage": "remix_rewrite"})
    prompt = rewrite_prompt(spec, source_text, rhythm, external, parent_episodes)
    plan = await llm.structured(SeriesPlan, REWRITE_RULES, bible, prompt, content_mode, effort="high",
                                model=model_for("remix_rewrite").model, stage="remix_rewrite")
    _check_plan(plan, content_mode)
    for i, e in enumerate(plan.episodes, 1):
        e.episode_number = i
    return plan
