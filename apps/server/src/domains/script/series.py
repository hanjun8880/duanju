"""Novel / idea / remix transcript -> series bible + episode outlines -> per-episode screenplay.

The source is cut locally into numbered segments (chapters, else ~3000-char chunks) so the planner only has to
name segment numbers per episode, and the script writer gets back exactly the text it adapts.
"""
import base64
import re
from pathlib import Path
from typing import Any

from src.domains.script.script_engine import EpisodeOutline, SeriesPlan

MAX_SOURCE_CHARS = 300_000  # shortcut: one planning call; split very long novels into volumes (分卷) for now
CHUNK = 3000
CHAPTER = re.compile(r"^\s*(第[0-9０-９零〇一二三四五六七八九十百千万两]+[章回节卷集]|chapter\s+\d+)", re.IGNORECASE | re.MULTILINE)

PLAN_RULES = (
    "你是连续剧总编剧兼制片人，把输入的小说原文、视频转写或一句创意，改编成竖屏 AI 短剧连续剧。"
    "每集 1-3 分钟：开场 3 秒内给出冲突钩子，结尾留悬念。先锁定全剧的人物、道具、场景设定（名称全剧唯一且固定，"
    "后续分镜只能引用这些名称），再给出分集大纲。所有角色都是虚构的成年人。"
    "visual_prompt 要具体到可以直接出定妆图：年龄段、脸型发型、服装材质与颜色、标志性配饰、整体气质。"
    "改编小说时，每集写明 source_segments（对应的【片段 i】编号，按顺序，不遗漏主线）；原创创意时 source_segments 为空。"
)
SCRIPT_RULES = (
    "你是短剧编剧。根据 SERIES_BIBLE 和本集大纲写出可拍摄的分场剧本。格式：每场以「场N 日/夜 内/外 地点」开头，"
    "下面是动作描写和「角色名：台词」。只使用 SERIES_BIBLE 里已有的人物、道具、场景名称。台词口语化、短句、有冲突。"
    "所有角色均为虚构成年人。"
)
KEYFRAME_RULES = (
    "你在为视频二创做逆向拆解。每张图是原片一个镜头的中位关键帧，附带该镜头的时间段和台词。"
    "逐镜头客观描述画面：人物外观与动作、场景、光线、景别与机位。不要猜测真实人物身份。"
)


def split_source(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    if len(text) > MAX_SOURCE_CHARS:
        raise ValueError(f"原文 {len(text)} 字，超过单次 {MAX_SOURCE_CHARS} 字上限，请分卷导入")
    starts = [m.start() for m in CHAPTER.finditer(text)]
    if len(starts) >= 2:
        if starts[0] > 0:
            starts.insert(0, 0)
        parts = [text[a:b].strip() for a, b in zip(starts, [*starts[1:], len(text)], strict=True)]
        return [p for p in parts if p]
    parts, buf = [], ""
    for para in text.split("\n"):
        if buf and len(buf) + len(para) > CHUNK:
            parts.append(buf.strip())
            buf = ""
        buf += para + "\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts


def plan_prompt(segments: list[str], episode_count: int | None) -> str:
    n = f"共 {episode_count} 集" if episode_count else "集数按内容体量自定（通常每 1-2 个片段一集）"
    if len(segments) == 1 and len(segments[0]) < 600:
        return f"原创创意（{n}）：\n{segments[0]}"
    body = "\n\n".join(f"【片段 {i}】\n{s}" for i, s in enumerate(segments, 1))
    return f"改编以下原文为连续剧（{n}）：\n\n{body}"


def bible_from_plan(plan: SeriesPlan) -> dict[str, Any]:
    """Byte-stable locked assets (sorted keys in bible_text); only what every later call must agree on."""
    return {"title": plan.title, "logline": plan.logline,
            "characters": [c.model_dump() for c in plan.characters],
            "props": [p.model_dump() for p in plan.props], "scenes": [s.model_dump() for s in plan.scenes]}


def script_prompt(outline: EpisodeOutline | dict[str, Any], segments: list[str], prev_synopsis: str | None) -> str:
    o = outline if isinstance(outline, dict) else outline.model_dump()
    src = "\n\n".join(f"【片段 {i}】\n{segments[i - 1]}" for i in o.get("source_segments", []) if 0 < i <= len(segments))
    prev = f"上一集梗概：{prev_synopsis}\n" if prev_synopsis else ""
    return (f"{prev}第 {o['episode_number']} 集《{o['title']}》大纲：{o['synopsis']}\n"
            + (f"\n改编依据原文：\n{src}" if src else "\n（原创，无原文）"))


def keyframe_content(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude image blocks (one keyframe per shot) interleaved with timing + dialogue text."""
    blocks: list[dict[str, Any]] = []
    for s in shots:
        blocks.append({"type": "text", "text": f"镜头 {s['shot_order']}（{s['start_time']}-{s['end_time']}s）"
                                               f"台词：{s['dialogue'] or '无'}"})
        img = base64.b64encode(Path(s["keyframe_path"]).read_bytes()).decode()
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}})
    blocks.append({"type": "text", "text": "按镜头顺序输出每个镜头的画面描述。"})
    return blocks


def remix_source_text(shots: list[dict[str, Any]], notes: dict[int, str]) -> str:
    """The remix becomes ordinary source text, so it flows through the same plan -> script -> storyboard path."""
    lines = ["【原片逆向拆解】"]
    for s in shots:
        desc = notes.get(s["shot_order"], "（画面未识别：未配置视觉模型）")
        lines.append(f"镜头{s['shot_order']}（{s['start_time']}-{s['end_time']}s）画面：{desc}"
                     + (f" 台词：{s['dialogue']}" if s["dialogue"] else ""))
    return "\n".join(lines)
