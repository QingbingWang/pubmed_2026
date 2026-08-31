"""End-to-end pipeline: question → PubMed query → abstracts TXT → LLM answer."""

from __future__ import annotations

import json
import re
from typing import Any

from services.glm_client import GLMClient, get_llm
from services.pubmed_client import PubMedClient


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from model output, tolerating markdown fences."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"无法解析模型返回的 JSON：{text[:300]}")


class Pipeline:
    def __init__(self) -> None:
        self.pubmed = PubMedClient()

    def llm(self, provider: str | None = None) -> GLMClient:
        return get_llm(provider)

    @property
    def glm(self) -> GLMClient:
        """Backward-compatible alias for the default provider client."""
        return self.llm()

    def build_query(self, question: str, provider: str | None = None) -> dict[str, Any]:
        raw = self.llm(provider).generate_pubmed_query(question)
        parsed = _extract_json(raw)
        keywords = parsed.get("keywords") or []
        pubmed_query = (parsed.get("pubmed_query") or "").strip()
        rationale = (parsed.get("rationale") or "").strip()
        if not pubmed_query:
            raise ValueError("模型未生成有效的 PubMed 检索式")
        if isinstance(keywords, str):
            keywords = [keywords]
        return {
            "keywords": keywords,
            "pubmed_query": pubmed_query,
            "rationale": rationale,
            "raw": raw,
        }

    def search_literature(
        self,
        pubmed_query: str,
        max_results: int | None = None,
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.pubmed.search_and_save(
            pubmed_query, max_results=max_results, keywords=keywords
        )

    def answer(
        self,
        question: str,
        abstracts_text: str,
        provider: str | None = None,
    ) -> dict:
        if not abstracts_text.strip():
            return {
                "answer": "未检索到可用摘要，无法基于文献作答。请调整检索式后重试。",
                "truncated": False,
                "uploaded_chars": 0,
                "original_chars": 0,
                "max_chars": 0,
            }
        return self.llm(provider).answer_from_abstracts(question, abstracts_text)

    def run(
        self,
        question: str,
        max_results: int | None = None,
        pubmed_query_override: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        if pubmed_query_override and pubmed_query_override.strip():
            query_info = {
                "keywords": [],
                "pubmed_query": pubmed_query_override.strip(),
                "rationale": "使用用户手动指定的检索式",
                "raw": "",
            }
        else:
            query_info = self.build_query(question, provider=provider)

        search_result = self.search_literature(
            query_info["pubmed_query"],
            max_results=max_results,
            keywords=query_info.get("keywords") or [],
        )
        result = self.answer(
            question, search_result.get("text") or "", provider=provider
        )

        return {
            "question": question,
            "keywords": query_info["keywords"],
            "pubmed_query": query_info["pubmed_query"],
            "rationale": query_info["rationale"],
            "pmids": search_result["pmids"],
            "count": search_result["count"],
            "filename": search_result["filename"],
            "filepath": search_result["filepath"],
            "abstracts_preview": (search_result.get("text") or "")[:4000],
            "answer": result.get("answer") if isinstance(result, dict) else result,
            "truncated": bool(result.get("truncated")) if isinstance(result, dict) else False,
            "uploaded_chars": result.get("uploaded_chars", 0) if isinstance(result, dict) else 0,
        }
