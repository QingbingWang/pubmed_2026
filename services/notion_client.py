"""Notion API client: create a database page from Q&A export."""

from __future__ import annotations

import re
from typing import Any

import requests

import config

NOTION_API = "https://api.notion.com/v1"
RICH_TEXT_LIMIT = 2000
BLOCK_TEXT_LIMIT = 1800
TITLE_LIMIT = 2000

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_UL_RE = re.compile(r"^[-*+]\s+(.+)$")
_OL_RE = re.compile(r"^(\d+)[.)]\s+(.+)$")
_DIVIDER_RE = re.compile(r"^-{3,}\s*$")
# **bold** / *italic* / `code`（先匹配加粗，避免与斜体冲突）
_INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*|__[^_]+__|"
    r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)|"
    r"(?<!_)_(?!_)([^_]+)_(?!_)|"
    r"`([^`]+)`)"
)


def _text_obj(content: str, *, bold: bool = False, italic: bool = False, code: bool = False) -> dict:
    content = content[:RICH_TEXT_LIMIT]
    ann: dict[str, Any] = {
        "bold": bold,
        "italic": italic,
        "strikethrough": False,
        "underline": False,
        "code": code,
        "color": "default",
    }
    return {"type": "text", "text": {"content": content}, "annotations": ann}


def parse_inline_rich_text(text: str) -> list[dict]:
    """Convert a line of Markdown inline markup into Notion rich_text."""
    if not text:
        return []
    parts: list[dict] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            parts.append(_text_obj(text[pos : m.start()]))
        token = m.group(0)
        if token.startswith("**") and token.endswith("**"):
            parts.append(_text_obj(token[2:-2], bold=True))
        elif token.startswith("__") and token.endswith("__"):
            parts.append(_text_obj(token[2:-2], bold=True))
        elif token.startswith("*") and token.endswith("*") and not token.startswith("**"):
            parts.append(_text_obj(token[1:-1], italic=True))
        elif token.startswith("_") and token.endswith("_") and not token.startswith("__"):
            parts.append(_text_obj(token[1:-1], italic=True))
        elif token.startswith("`") and token.endswith("`"):
            parts.append(_text_obj(token[1:-1], code=True))
        else:
            parts.append(_text_obj(token))
        pos = m.end()
    if pos < len(text):
        parts.append(_text_obj(text[pos:]))
    # Notion: each rich_text.content max 2000; merge/split safely
    return _split_rich_text(parts)


def _split_rich_text(parts: list[dict]) -> list[dict]:
    out: list[dict] = []
    for p in parts:
        content = p.get("text", {}).get("content") or ""
        ann = p.get("annotations") or {}
        bold = bool(ann.get("bold"))
        italic = bool(ann.get("italic"))
        code = bool(ann.get("code"))
        if len(content) <= RICH_TEXT_LIMIT:
            if content:
                out.append(_text_obj(content, bold=bold, italic=italic, code=code))
            continue
        start = 0
        while start < len(content):
            out.append(
                _text_obj(
                    content[start : start + RICH_TEXT_LIMIT],
                    bold=bold,
                    italic=italic,
                    code=code,
                )
            )
            start += RICH_TEXT_LIMIT
    return out


def _block(block_type: str, rich_text: list[dict]) -> dict:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text or [_text_obj("")]},
    }


def _paragraph_blocks(text: str) -> list[dict]:
    """Split long paragraph into multiple paragraph blocks."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= BLOCK_TEXT_LIMIT:
        return [_block("paragraph", parse_inline_rich_text(text))]
    blocks: list[dict] = []
    start = 0
    while start < len(text):
        chunk = text[start : start + BLOCK_TEXT_LIMIT]
        blocks.append(_block("paragraph", parse_inline_rich_text(chunk)))
        start += BLOCK_TEXT_LIMIT
    return blocks


def markdown_to_blocks(md: str) -> list[dict]:
    """Parse Markdown-ish answer into Notion blocks (headings / divider / lists / paragraphs)."""
    text = (md or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    blocks: list[dict] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        if _DIVIDER_RE.match(stripped):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue

        hm = _HEADING_RE.match(stripped)
        if hm:
            level = len(hm.group(1))
            heading_type = {1: "heading_1", 2: "heading_2", 3: "heading_3"}[level]
            blocks.append(_block(heading_type, parse_inline_rich_text(hm.group(2))))
            continue

        um = _UL_RE.match(stripped)
        if um:
            blocks.append(_block("bulleted_list_item", parse_inline_rich_text(um.group(1))))
            continue

        om = _OL_RE.match(stripped)
        if om:
            blocks.append(_block("numbered_list_item", parse_inline_rich_text(om.group(2))))
            continue

        # 普通行各自成段，便于「**适用场景：** …」单独成行显示
        blocks.extend(_paragraph_blocks(stripped))

    return blocks


class NotionClient:
    def __init__(self) -> None:
        if not config.notion_configured():
            raise ValueError(
                "请在 .env 中配置 NOTION_API_KEY 与 NOTION_DATABASE_ID，"
                "并将数据库 Share 给该 Integration"
            )
        self.api_key = config.NOTION_API_KEY
        self.database_id = self._normalize_id(config.NOTION_DATABASE_ID)
        self.version = config.NOTION_VERSION
        self._schema: dict[str, dict] | None = None
        self._title_prop: str | None = None

    @staticmethod
    def _normalize_id(raw: str) -> str:
        s = raw.strip().replace("-", "")
        if len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
            return (
                f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"
            ).lower()
        return raw.strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = f"{NOTION_API}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=60, **kwargs
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Notion 网络请求失败：{exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            data = {"message": resp.text}

        if not resp.ok:
            msg = data.get("message") or data.get("error") or resp.text
            raise RuntimeError(f"Notion API 错误 ({resp.status_code})：{msg}")
        return data if isinstance(data, dict) else {"data": data}

    def retrieve_database(self) -> dict:
        return self._request("GET", f"/databases/{self.database_id}")

    def load_schema(self) -> dict[str, dict]:
        if self._schema is not None:
            return self._schema
        db = self.retrieve_database()
        props = db.get("properties") or {}
        self._schema = props
        for name, meta in props.items():
            if meta.get("type") == "title":
                self._title_prop = name
                break
        if not self._title_prop:
            raise RuntimeError("目标数据库未找到标题（title）属性")
        return self._schema

    def title_property_name(self) -> str:
        self.load_schema()
        assert self._title_prop
        return self._title_prop

    @staticmethod
    def _rich_text(content: str) -> list[dict]:
        text = (content or "")[:RICH_TEXT_LIMIT]
        if not text:
            return []
        return [{"type": "text", "text": {"content": text}}]

    def _build_properties(self, payload: dict[str, Any]) -> dict[str, Any]:
        schema = self.load_schema()
        title_name = self.title_property_name()
        title = (payload.get("title") or payload.get("question") or "").strip()
        if not title:
            raise ValueError("标题（提问）不能为空")

        props: dict[str, Any] = {
            title_name: {
                "title": self._rich_text(title[:TITLE_LIMIT])
            }
        }

        def has(name: str, typ: str) -> bool:
            meta = schema.get(name)
            return bool(meta and meta.get("type") == typ)

        summary = (payload.get("summary") or "").strip()
        if summary and has("一句话总结", "rich_text"):
            props["一句话总结"] = {"rich_text": self._rich_text(summary)}

        learn_date = (payload.get("learn_date") or "").strip()
        if learn_date and has("学习日期", "date"):
            props["学习日期"] = {"date": {"start": learn_date}}

        source_name = (payload.get("source_name") or "").strip()
        if source_name and has("来源名称", "rich_text"):
            props["来源名称"] = {"rich_text": self._rich_text(source_name)}

        source_type = (payload.get("source_type") or "").strip()
        if source_type and has("来源类型", "select"):
            props["来源类型"] = {"select": {"name": source_type}}

        tags = payload.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.replace("；", ",").split(",") if t.strip()]
        tags = [str(t).strip() for t in tags if str(t).strip()]
        if tags and has("标签", "multi_select"):
            props["标签"] = {"multi_select": [{"name": t} for t in tags]}

        status = (payload.get("status") or "").strip()
        if status and has("状态", "status"):
            props["状态"] = {"status": {"name": status}}

        importance = (payload.get("importance") or "").strip()
        if importance and has("重要性", "select"):
            props["重要性"] = {"select": {"name": importance}}

        link = (payload.get("url") or payload.get("link") or "").strip()
        if link and has("链接/DOI", "url"):
            props["链接/DOI"] = {"url": link}

        return props

    def _build_children(self, answer: str) -> list[dict]:
        """Turn Markdown answer into Notion structured blocks (图1 样式)."""
        return markdown_to_blocks(answer)

    def create_qa_page(self, payload: dict[str, Any]) -> dict[str, str]:
        """Create a Notion database page; returns page_id and page_url."""
        answer = (payload.get("answer") or "").strip()
        if not answer:
            raise ValueError("回答内容不能为空")

        properties = self._build_properties(payload)
        children = self._build_children(answer)

        # Notion create page accepts up to 100 children
        first_children = children[:100]
        rest = children[100:]

        body: dict[str, Any] = {
            "parent": {"database_id": self.database_id},
            "properties": properties,
        }
        if first_children:
            body["children"] = first_children

        page = self._request("POST", "/pages", json=body)
        page_id = page.get("id") or ""
        page_url = page.get("url") or ""

        # Append remaining blocks if answer is extremely long
        while rest and page_id:
            batch = rest[:100]
            rest = rest[100:]
            self._request(
                "PATCH",
                f"/blocks/{page_id}/children",
                json={"children": batch},
            )

        return {"page_id": page_id, "page_url": page_url}


def get_notion_client() -> NotionClient:
    return NotionClient()
