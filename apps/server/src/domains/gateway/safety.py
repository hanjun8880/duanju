"""Pre-Flight Guard: zero-tolerance minors block + audit log. Local regex only (no model call).

shortcut: keyword/regex list, not a classifier; extend MINOR_PATTERNS or add an image classifier when
user-uploaded images are screened.
"""
import json
import logging
import re
from typing import Any

audit = logging.getLogger("audit")

MINOR_PATTERNS = [
    r"\bunder[\s-]?age(d)?\b", r"\bminors?\b", r"\bchild(ren)?\b", r"\bkids?\b", r"\btoddlers?\b",
    r"\binfants?\b", r"\bpre[\s-]?teens?\b", r"\bteen(s|ager|agers|age)?\b", r"\bschool\s?(girl|boy)s?\b",
    r"\bloli(ta|con)?\b", r"\bshota(con)?\b", r"\bjailbait\b", r"\b(elementary|middle|junior high|high)\s+school",
    r"\b(1[0-7]|[1-9])\s*[- ]?(yo|y/o|years?[\s-]old)\b",
    r"未成年", r"幼女", r"幼童", r"儿童", r"小孩", r"萝莉", r"正太", r"童颜", r"少女", r"少年",
    r"小学生", r"初中生", r"中学生", r"高中生", r"(?<!\d)(1[0-7]|[1-9])\s*(岁|周岁)",
]
SEXUAL_PATTERNS = [r"\bsex(y|ual)?\b", r"\bnude\b", r"\bnaked\b", r"\berotic\b", r"\bseductive\b", r"\bnsfw\b",
                   r"裸", r"性感", r"色情", r"情色", r"亲密", r"诱惑", r"床戏"]
_minor = re.compile("|".join(MINOR_PATTERNS), re.IGNORECASE)
_sexual = re.compile("|".join(SEXUAL_PATTERNS), re.IGNORECASE)


class ContentBlocked(Exception):
    pass


def check_prompt(text: str, content_mode: str, context: dict[str, Any] | None = None) -> None:
    """uncensored_nsfw: any minor indicator blocks. standard_sfw: minor indicator + sexual term blocks."""
    m = _minor.search(text)
    if m and (content_mode == "uncensored_nsfw" or _sexual.search(text)):
        audit.warning(json.dumps({"event": "content_blocked", "rule": "minor", "match": m.group(0),
                                  "content_mode": content_mode, **(context or {})}, ensure_ascii=False))
        raise ContentBlocked("prompt references minors; only fictional adult characters are allowed")
