"""Turn an uploaded file or a web page into plain source text, without an LLM call.

shortcut: webpage extraction is a plain heuristic (strip chrome tags, keep text-bearing blocks), not a
full readability algorithm; good enough for article/novel-chapter pages, may miss unusual layouts.
"""
import re
from io import BytesIO

import httpx
from lxml import html as lxml_html

MAX_FETCH_BYTES = 8_000_000
DROP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg", "button"}
BLOCK_TAGS = {"p", "div", "li", "td", "blockquote", "h1", "h2", "h3", "h4", "pre", "br"}


def extract_file_text(filename: str, content: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if ext == "docx":
        from docx import Document
        doc = Document(BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if ext == "txt":
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gb18030")
    raise ValueError(f"unsupported file type .{ext}; use .txt/.pdf/.docx")


def extract_webpage_text(html_doc: str) -> str:
    """Drop chrome tags, keep block-level text, collapse blocks under 15 chars (nav links, buttons)."""
    try:
        tree = lxml_html.fromstring(html_doc)
    except Exception as e:
        raise ValueError(f"无法解析该网页：{e}") from e
    for tag in DROP_TAGS:
        for el in tree.xpath(f"//{tag}"):
            el.drop_tree()
    lines: list[str] = []
    headings = {"h1", "h2", "h3", "h4"}
    for el in tree.iter():
        if el.tag not in BLOCK_TAGS:
            continue
        text = " ".join((el.text_content() or "").split())
        floor = 4 if el.tag in headings else 15  # headings are legitimately short; everything else is likely chrome
        if len(text) >= floor and text not in (lines[-1] if lines else ""):
            lines.append(text)
    body = "\n\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 50:
        raise ValueError("抓取到的正文过短，可能是该页面需要登录或为非文章页面")
    return body


async def fetch_url_text(client: httpx.AsyncClient, url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError("请输入 http(s) 链接")
    async with client.stream("GET", url, follow_redirects=True, timeout=30,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; DuanjuBot/1.0)"}) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise ValueError(f"链接不是网页正文（content-type: {ctype or '未知'}）")
        chunks, size = [], 0
        async for chunk in r.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FETCH_BYTES:
                break
        raw = b"".join(chunks)
    try:
        charset = r.encoding or "utf-8"
        doc = raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        doc = raw.decode("utf-8", errors="replace")
    return extract_webpage_text(doc)
