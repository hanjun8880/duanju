"""Novel / idea / remix transcript -> series bible + episode outlines -> per-episode screenplay.

The source is cut locally into numbered segments (chapters, else ~3000-char chunks) so the planner only has to
name segment numbers per episode, and the script writer gets back exactly the text it adapts.
"""
import asyncio
import base64
import hashlib
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from src.domains.script.script_engine import (
    CachedDirectorLLM,
    DigestBatch,
    EpisodeOutline,
    EpisodeOutlines,
    SeriesPlan,
    SeriesSetting,
)

MAX_SOURCE_CHARS = 2_000_000
SINGLE_PASS_CHARS = 40_000  # up to here one planning call sees the whole text; above it, two-stage planning
CHUNK = 3000
MAX_SEGMENT = 4 * CHUNK  # oversized chapters are cut further so a 4-segment summarize batch stays small
DIGEST_BATCH = 4
DIGEST_CONCURRENCY = 3
OUTLINE_BATCH = 20
CHAPTER = re.compile(r"^\s*(第[0-9０-９零〇一二三四五六七八九十百千万两]+[章回节卷集]|chapter\s+\d+)", re.IGNORECASE | re.MULTILINE)
EPISODE_MARK = re.compile(r"^\s*第[0-9０-９零〇一二三四五六七八九十百千万两]+集", re.MULTILINE)

REFORMAT_RULES = (
    "你是短剧编剧。把输入文本整理成可直接拍摄的分场剧本，不改变剧情、台词内容和人物，只整理格式。"
    "格式：每场以「场N 日/夜 内/外 地点」开头，下面是动作描写和「角色名：台词」。台词口语化、短句。"
    "所有角色均为虚构成年人。"
)

PLAN_RULES = (
    "你是连续剧总编剧兼制片人，把输入的小说原文、视频转写或一句创意，改编成竖屏 AI 短剧连续剧。"
    "每集 1-3 分钟：开场 3 秒内给出冲突钩子，结尾留悬念。先锁定全剧的人物、道具、场景设定（名称全剧唯一且固定，"
    "后续分镜只能引用这些名称），再给出分集大纲。所有角色都是虚构的成年人。"
    "visual_prompt 要具体到可以直接出定妆图：年龄段、脸型发型、服装材质与颜色、标志性配饰、整体气质。"
    "改编小说时，每集写明 source_segments（对应的【片段 i】编号，按顺序，不遗漏主线）；原创创意时 source_segments 为空。"
)
SUMMARIZE_RULES = (
    "你是小说编辑，为改编竖屏短剧连续剧做阅读笔记。对输入的每个【片段 i】各输出一条：segment=i，"
    "200 字内剧情摘要（保留关键情节、转折、伏笔、人物关系变化），出场人物（统一用全名）、场景、关键道具。"
    "不要遗漏任何片段，不要合并片段。所有角色都是虚构的成年人。"
)
SETTING_RULES = (
    "你是连续剧总编剧兼制片人。输入是整部小说逐片段的摘要。把它改编成竖屏 AI 短剧连续剧：先锁定全剧的人物、道具、"
    "场景设定（名称全剧唯一且固定，后续分镜只能引用这些名称），并给出建议总集数（每集 1-3 分钟）。"
    "visual_prompt 要具体到可以直接出定妆图：年龄段、脸型发型、服装材质与颜色、标志性配饰、整体气质。"
    "所有角色都是虚构的成年人。"
)
OUTLINE_RULES = (
    "你是连续剧总编剧。根据 SERIES_BIBLE（已锁定的设定，只能用其中的名称）和下面的全书片段摘要，写指定范围的分集大纲。"
    "每集 1-3 分钟：开场 3 秒内给出冲突钩子，结尾留悬念；剧情按原著顺序推进，不遗漏主线。"
    "每集写明 source_segments（对应的【片段 i】编号，按顺序）。所有角色都是虚构的成年人。"
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
        return [c for p in parts if p for c in (_chunks(p) if len(p) > MAX_SEGMENT else [p])]
    return _chunks(text)


def _chunks(text: str) -> list[str]:
    parts, buf = [], ""
    for para in text.split("\n"):
        while len(para) > MAX_SEGMENT:  # one giant paragraph: hard cut
            if buf.strip():
                parts.append(buf.strip())
                buf = ""
            parts.append(para[:CHUNK])
            para = para[CHUNK:]
        if buf and len(buf) + len(para) > CHUNK:
            parts.append(buf.strip())
            buf = ""
        buf += para + "\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts


def split_episodes(text: str) -> list[str]:
    """Strict split for an already-written script: only explicit '第N集' markers, no size fallback."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    starts = [m.start() for m in EPISODE_MARK.finditer(text)]
    if len(starts) < 2:
        return [text]
    if starts[0] > 0:
        starts.insert(0, 0)
    return [p for p in (text[a:b].strip() for a, b in zip(starts, [*starts[1:], len(text)], strict=True)) if p]


def episode_title(segment: str, n: int) -> str:
    mark = EPISODE_MARK.match(segment)
    first_line = segment.split("\n", 1)[0].strip()
    rest = first_line[mark.end():].strip() if mark else first_line
    return rest if 0 < len(rest) <= 30 else f"第{n}集"


def plan_prompt(segments: list[str], episode_count: int | None, direction: str | None = None) -> str:
    n = f"共 {episode_count} 集" if episode_count else "集数按内容体量自定（通常每 1-2 个片段一集）"
    d = f"\n创作方向：{direction}" if direction and direction.strip() else ""
    if len(segments) == 1 and len(segments[0]) < 600:
        return f"原创创意（{n}）：\n{segments[0]}{d}"
    body = "\n\n".join(f"【片段 {i}】\n{s}" for i, s in enumerate(segments, 1))
    return f"改编以下原文为连续剧（{n}）：{d}\n\n{body}"


def bible_from_plan(plan: SeriesPlan | SeriesSetting, keep: dict[str, Any] | None = None) -> dict[str, Any]:
    """Byte-stable locked assets (sorted keys in bible_text); only what every later call must agree on.
    Bookkeeping keys ("_cache" digests, "_progress") of the previous bible are carried over, never prompted."""
    return {"title": plan.title, "logline": plan.logline,
            "characters": [c.model_dump() for c in plan.characters],
            "props": [p.model_dump() for p in plan.props], "scenes": [s.model_dump() for s in plan.scenes],
            **{k: v for k, v in (keep or {}).items() if k.startswith("_")}}


def needs_two_stage(segments: list[str]) -> bool:
    return sum(len(s) for s in segments) > SINGLE_PASS_CHARS


def seg_hash(segment: str) -> str:
    return hashlib.sha256(segment.encode()).hexdigest()


def digest_prompt(batch: list[tuple[int, str]]) -> str:
    return "为以下片段逐个写阅读笔记：\n\n" + "\n\n".join(f"【片段 {i}】\n{s}" for i, s in batch)


def digests_text(digests: list[dict[str, Any]]) -> str:
    def names(d: dict[str, Any], k: str) -> str:
        return "、".join(d.get(k) or []) or "无"
    return "\n".join(f"【片段 {i}】{d['summary']}（人物：{names(d, 'characters')}；场景：{names(d, 'scenes')}；"
                     f"道具：{names(d, 'props')}）" for i, d in enumerate(digests, 1))


def setting_prompt(digests: str, episode_count: int | None, direction: str | None) -> str:
    n = f"共 {episode_count} 集" if episode_count else "集数按内容体量自定（通常每 1-2 个片段一集）"
    d = f"\n创作方向：{direction}" if direction and direction.strip() else ""
    return f"改编以下全书摘要为连续剧（{n}）：{d}\n\n{digests}"


def outline_prompt(start: int, end: int, total: int, n_segments: int, prev: list[EpisodeOutline],
                   direction: str | None) -> str:
    lo = (start - 1) * n_segments // total + 1
    hi = max(lo, -(-end * n_segments // total))
    before = "\n".join(f"第{o.episode_number}集《{o.title}》：{o.synopsis}" for o in prev[-3:])
    d = f"\n创作方向：{direction}" if direction and direction.strip() else ""
    return (f"写第 {start}-{end} 集（全剧共 {total} 集）的分集大纲，本批大致对应【片段 {lo}】到【片段 {hi}】。{d}"
            + (f"\n前情（上一批最后几集）：\n{before}" if before else "\n这是全剧开头。"))


Progress = Callable[[dict[str, Any]], Awaitable[None]]


async def two_stage_plan(llm: CachedDirectorLLM, segments: list[str], episode_count: int | None,
                         direction: str | None, content_mode: str, cache: dict[str, Any],
                         progress: Progress, tier: str | None = None,
                         summarize_tier: str | None = None) -> SeriesPlan:
    """summarize (cached per segment sha256, <=3 concurrent) -> plan the bible -> outline <=20 episodes a call.
    `cache` is updated in place (pruned to these segments); `progress` is awaited after every step.
    `tier` picks the plan model; `summarize_tier` independently selects the reading model.
    Existing segment digests remain reusable when changing tiers."""
    hashes = [seg_hash(s) for s in segments]
    todo = [i for i, h in enumerate(hashes, 1) if h not in cache]
    total = len(segments)
    done = total - len(todo)
    lock, sem = asyncio.Lock(), asyncio.Semaphore(DIGEST_CONCURRENCY)
    await progress({"stage": "summarize", "done": done, "total": total})

    async def summarize(batch: list[int]) -> None:
        nonlocal done
        async with sem:
            res = await llm.structured(DigestBatch, SUMMARIZE_RULES, {},
                                       digest_prompt([(i, segments[i - 1]) for i in batch]), content_mode,
                                       effort="low", max_tokens=8000, stage="summarize", tier=summarize_tier)
        got = {d.segment: d for d in res.digests}
        async with lock:
            for i in batch:
                d = got.get(i)
                cache[hashes[i - 1]] = (d.model_dump(exclude={"segment"}) if d else  # never silently drop one
                                        {"summary": segments[i - 1][:300], "characters": [], "scenes": [],
                                         "props": []})
            done += len(batch)
            await progress({"stage": "summarize", "done": done, "total": total})

    await asyncio.gather(*(summarize(todo[k:k + DIGEST_BATCH]) for k in range(0, len(todo), DIGEST_BATCH)))
    for h in [h for h in cache if h not in set(hashes)]:
        del cache[h]
    digests = digests_text([cache[h] for h in hashes])

    await progress({"stage": "plan", "done": 0, "total": 1})
    setting: SeriesSetting = await llm.structured(SeriesSetting, SETTING_RULES, {},
                                                  setting_prompt(digests, episode_count, direction),
                                                  content_mode, effort="high", stage="plan", tier=tier)
    n = max(1, min(200, episode_count or setting.episode_count))
    bible = bible_from_plan(setting)
    rules = f"{OUTLINE_RULES}\n\n全书片段摘要：\n{digests}"  # byte-stable across batches -> cached prefix
    episodes: list[EpisodeOutline] = []
    for start in range(1, n + 1, OUTLINE_BATCH):
        end = min(n, start + OUTLINE_BATCH - 1)
        await progress({"stage": "outline", "done": len(episodes), "total": n})
        out: EpisodeOutlines = await llm.structured(EpisodeOutlines, rules, bible,
                                                    outline_prompt(start, end, n, total, episodes, direction),
                                                    content_mode, effort="medium", stage="plan", tier=tier)
        batch = sorted(out.episodes, key=lambda o: o.episode_number)[:end - start + 1]
        episodes += [o.model_copy(update={"episode_number": len(episodes) + 1 + k}) for k, o in enumerate(batch)]
    return SeriesPlan(title=setting.title, logline=setting.logline, characters=setting.characters,
                      props=setting.props, scenes=setting.scenes, episodes=episodes)


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
