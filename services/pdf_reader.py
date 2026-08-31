"""Extract and structure PDF text into chapters / paragraphs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

# typical academic heading patterns
_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:chapter|section|part)\s+[\dIVXLC]+(?:[\.\):\s].*)?|"
    r"\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+\S.*|"
    r"(?:abstract|introduction|methods?|materials?\s+and\s+methods?|"
    r"results?|discussion|conclusion|conclusions?|references?|"
    r"acknowledg(?:e)?ments?|supplementary|background|"
    r"objectives?|关键词|摘要|引言|方法|结果|讨论|结论|参考文献)"
    r")$",
    re.IGNORECASE,
)


def list_pdfs(folder: Path) -> list[dict[str, Any]]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise ValueError(f"文件夹不存在：{folder}")
    files = sorted(folder.glob("*.pdf"), key=lambda p: p.name.lower())
    return [
        {
            "filename": f.name,
            "filepath": str(f),
            "size": f.stat().st_size,
        }
        for f in files
    ]


def _is_heading(line: str) -> bool:
    text = line.strip()
    if not text or len(text) > 120:
        return False
    if len(text) < 3:
        return False
    # mostly digits / punctuation → not heading
    if re.fullmatch(r"[\d\W]+", text):
        return False
    if _HEADING_RE.match(text):
        return True
    # short Title Case / ALL CAPS line without ending period
    if text.endswith((".", ",", ";", ":")) and len(text) > 40:
        return False
    words = text.split()
    if 1 <= len(words) <= 12:
        letters = [c for c in text if c.isalpha()]
        if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.55:
            return True
        if text[:1].isupper() and not text.endswith(".") and len(text) <= 80:
            # numbered like "2 Related Work"
            if re.match(r"^\d+(\.\d+)*\s+\S", text):
                return True
    return False


def _extract_raw_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        text = text.strip()
        if text:
            parts.append(text)
        else:
            parts.append(f"[第 {i + 1} 页无可提取文本]")
    return "\n\n".join(parts)


def _normalize_paragraphs(raw: str) -> list[str]:
    # unify newlines, split on blank lines
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # join hyphenated line breaks common in PDFs
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    chunks = re.split(r"\n\s*\n+", text)
    paras: list[str] = []
    for chunk in chunks:
        cleaned = re.sub(r"[ \t]+", " ", chunk)
        cleaned = re.sub(r"\n+", " ", cleaned).strip()
        if cleaned:
            paras.append(cleaned)
    return paras


def structure_pdf(path: Path) -> dict[str, Any]:
    """Return structured blocks for click-to-translate UI."""
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("不是有效的 PDF 文件")

    raw = _extract_raw_text(path)
    paras = _normalize_paragraphs(raw)

    blocks: list[dict[str, Any]] = []
    current_chapter: dict[str, Any] | None = None
    chapter_paras: list[str] = []
    ch_idx = 0
    p_idx = 0

    def flush_chapter() -> None:
        nonlocal current_chapter, chapter_paras, ch_idx
        if current_chapter is None:
            return
        children = []
        for j, para in enumerate(chapter_paras):
            children.append(
                {
                    "id": f"c{ch_idx}p{j}",
                    "type": "paragraph",
                    "text": para,
                }
            )
        current_chapter["children"] = children
        current_chapter["text"] = "\n\n".join(
            [current_chapter["title"], *chapter_paras]
        ).strip()
        blocks.append(current_chapter)
        ch_idx += 1
        current_chapter = None
        chapter_paras = []

    for para in paras:
        if _is_heading(para):
            flush_chapter()
            current_chapter = {
                "id": f"c{ch_idx}",
                "type": "chapter",
                "title": para,
                "text": para,
                "children": [],
            }
            chapter_paras = []
        elif current_chapter is not None:
            chapter_paras.append(para)
        else:
            blocks.append(
                {
                    "id": f"p{p_idx}",
                    "type": "paragraph",
                    "text": para,
                }
            )
            p_idx += 1

    flush_chapter()

    return {
        "filename": path.name,
        "filepath": str(path),
        "page_hint": len(PdfReader(str(path)).pages),
        "block_count": len(blocks),
        "blocks": blocks,
    }
