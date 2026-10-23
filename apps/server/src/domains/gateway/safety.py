"""Pre-Flight Guard: zero-tolerance minors block + audit log. Local regex only (no model call).

Three term classes:
- STRONG: unambiguous minor indicators (未成年, 初中生, loli, ages under 18 ...).
- AMBIGUOUS: 少年 / 少女, which are also common adult idioms (少年将军, 少女时代, 少女感).
- SEXUAL: sexual-content terms.
Rules (both content modes): any minor indicator (strong, or ambiguous even inside an idiom) together with
sexual content blocks; the only release is an ambiguous term inside a known adult idiom plus an explicit adult
age/marker. In uncensored_nsfw, a strong term or a bare (non-idiom) ambiguous term blocks on its own.

shortcut: keyword/regex list, not a classifier; extend the lists or add an image classifier when
user-uploaded images are screened.
"""
import json
import logging
import re
from typing import Any

audit = logging.getLogger("audit")

_CN_AGE = r"(?<![十百零〇一二三四五六七八九两])(?:[一二三四五六七八九两]|十[一二三四五六七]?)\s*(?:岁|周岁)"
STRONG_PATTERNS = [
    r"\bunder[\s-]?age(d)?\b", r"\bminors?\b", r"\bchild(ren)?\b", r"\bkids?\b", r"\btoddlers?\b",
    r"\binfants?\b", r"\bpre[\s-]?teens?\b", r"\bteen(s|ager|agers|age)?\b", r"\bschool\s?(girl|boy)s?\b",
    r"\bloli(ta|con)?\b", r"\bshota(con)?\b", r"\bjailbait\b", r"\b(elementary|middle|junior high|high)\s+school",
    r"\b(1[0-7]|[1-9])\s*[- ]?(yo|y/o|years?[\s-]old)\b",
    r"未成年", r"幼女", r"幼童", r"儿童", r"小孩", r"孩子", r"萝莉", r"正太", r"童颜",
    r"小学生", r"初中生", r"中学生", r"高中生", r"(?<!\d)(1[0-7]|[1-9])\s*(岁|周岁)", _CN_AGE,
]
MINOR_PATTERNS = STRONG_PATTERNS  # backwards-compatible name
AMBIGUOUS_PATTERNS = [r"少年", r"少女"]
# Adult idioms: the ambiguous term here describes an adult (title, era, vibe) or an adult group.
ADULT_IDIOMS = [r"少年将军", r"少年英雄", r"少年侠客", r"少年天子", r"少年皇帝", r"少年宰相", r"少年得志",
                r"少女时代", r"少女感", r"少女心"]
# English uses of "minor" that are not about age.
NON_AGE_PHRASES = [r"\bminor\s+(characters?|roles?|injur(y|ies)|details?|issues?|keys?|chords?|changes?|parts?|scales?)\b"]
ADULT_MARKERS = [r"(?<!未)成年", r"\badults?\b", r"(?<!\d)([2-9]\d|1[89])\s*(岁|周岁|years?[\s-]old|yo)",
                 r"(?<![十百])(?:[二三四五六七八九]十[一二三四五六七八九]?|十[八九])\s*(?:岁|周岁)"]
SEXUAL_PATTERNS = [r"\bsex(y|ual)?\b", r"\bnude\b", r"\bnaked\b", r"\berotic\b", r"\bseductive\b", r"\bnsfw\b",
                   r"裸", r"性感", r"色情", r"情色", r"亲密", r"诱惑", r"床戏"]


def _rx(pats: list[str]) -> re.Pattern[str]:
    return re.compile("|".join(pats), re.IGNORECASE)


_strong, _ambig, _idiom = _rx(STRONG_PATTERNS), _rx(AMBIGUOUS_PATTERNS), _rx(ADULT_IDIOMS)
_non_age, _adult, _sexual = _rx(NON_AGE_PHRASES), _rx(ADULT_MARKERS), _rx(SEXUAL_PATTERNS)
_minor = _strong  # kept for callers that imported the old name


class ContentBlocked(Exception):
    pass


def _block(match: str, rule: str, content_mode: str, context: dict[str, Any] | None) -> None:
    audit.warning(json.dumps({"event": "content_blocked", "rule": rule, "match": match,
                              "content_mode": content_mode, **(context or {})}, ensure_ascii=False))
    raise ContentBlocked("prompt references minors; only fictional adult characters are allowed")


def check_prompt(text: str, content_mode: str, context: dict[str, Any] | None = None) -> None:
    """Raises ContentBlocked (and writes an audit line) when the prompt may involve minors (see module doc)."""
    masked = _non_age.sub(" ", text)
    strong = _strong.search(masked)
    sexual = _sexual.search(text)
    bare_ambig = _ambig.search(_idiom.sub(" ", masked))  # 少年/少女 outside adult idioms
    idiom = _idiom.search(masked)
    if strong and (sexual or content_mode == "uncensored_nsfw"):
        _block(strong.group(0), "minor", content_mode, context)
    if bare_ambig and (sexual or content_mode == "uncensored_nsfw"):
        _block(bare_ambig.group(0), "minor_ambiguous", content_mode, context)
    if idiom and sexual and not _adult.search(text):
        _block(idiom.group(0), "minor_idiom_sexual", content_mode, context)
