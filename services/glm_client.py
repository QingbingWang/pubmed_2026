"""OpenAI-compatible LLM client (GLM / DeepSeek)."""

from __future__ import annotations

from openai import OpenAI

import config

QUERY_SYSTEM_PROMPT = """你是一位精通 PubMed / MEDLINE 检索语法的医学信息专家。
根据用户的自然语言需求，完成三件事：
1. 理解语义并提取核心检索关键词（中英文均可，优先英文 MeSH / 常用术语）
2. 生成可直接用于 PubMed 的检索式（使用 AND / OR / NOT、字段标签如 [Title/Abstract]、[MeSH Terms] 等）
3. 简要说明检索思路

请严格按以下 JSON 格式输出，不要包含其它文字或代码块标记：
{
  "keywords": ["关键词1", "关键词2"],
  "pubmed_query": "完整的 PubMed 检索式",
  "rationale": "简短中文说明"
}"""

ANSWER_SYSTEM_PROMPT = """你是一位严谨的医学文献助手，正在对一批 PubMed Abstract 做系统综述式回答。
请仅依据用户提供的 Abstract 文本回答问题。
要求：
1. 先通读全部摘要，按主题归类后再作答；不要只挑前几篇或个别高相关文献
2. 对证据做综合：一致结论、矛盾结果、样本量/人群差异、方法局限都要交代
3. 尽量覆盖提供的全部相关文献；若篇数很多，按主题分组引用代表性文献，并说明“其余文献结论类似/相反”
4. 每个关键结论都必须有文献依据
5. 引用文献时必须同时给出：杂志名称、发表时间、PMID（取自摘要中的 JT 或 TA、DP、PMID 字段）
6. 正文引用统一使用以下格式（中文逗号分隔）：
   （杂志名称，发表时间，PMID）
   示例：（BMC medical informatics and decision making，2025-03，40055694）
7. 回答末尾增加「参考文献」列表，尽量列出本次作答实际用到的文献，每条一行，格式：
   [n] 杂志名称，发表时间，PMID | 标题: ...
8. 若某字段在摘要中缺失，写「未提供」，不得编造
9. 若证据不足或矛盾，明确说明
10. 不要编造摘要中未出现的数据或结论
11. 使用清晰的中文回答，必要时保留关键英文术语
12. 全文最后一句务必注明：本次共分析了多少篇文献摘要（以提供文本中的 PMID- 条目数为准；若用户提示给出了篇数，以该数字为准）"""



STUDY_QA_SYSTEM_PROMPT = """你是一位严谨的医学/科研文献助手。
请仅依据用户提供的 PDF 文献全文（或可提取文本）回答问题。
要求：
1. 结论必须有原文依据，可引用原文关键句子
2. 若证据不足或原文未涉及，明确说明
3. 不要编造原文未出现的数据或结论
4. 使用清晰的中文回答，必要时保留关键英文术语"""

DEEP_THINK_SYSTEM_PROMPT = """你是一位资深医学/科研顾问与学术写作助手。
用户会提供若干篇 PDF 文献的可提取全文，以及一段「思路/要求」。
请你：
1. 先通读、分析并归纳所选文献的核心发现、方法、证据强度与局限
2. 严格依据原文，不要编造未出现的数据、结论或引用
3. 按用户给出的思路/要求，产出具体、可执行、结构完整的研究计划或文章初稿
4. 关键论断必须标明引文：正文中用（文献文件名）标注依据；必要时摘录原文要点
5. 文末必须设置「参考文献 / 引文」列表，逐条列出本次实际用到的文献，格式：
   [n] 文献文件名 | 标题或主题（若原文可识别） | 关键依据摘要
6. 若证据不足或文献未覆盖用户要求的部分，明确指出缺口与可行补救建议
7. 使用清晰的中文输出，必要时保留关键英文术语；结构分明，便于直接落地执行或继续修改"""

ANSWER_MAX_CHARS = 3_000_000
ANSWER_TIMEOUT_SEC = 600.0


def clip_answer_context(text: str) -> tuple[str, bool, int, int]:
    """Return (clipped_text, truncated, uploaded_chars, original_chars)."""
    original = len(text or "")
    if original > ANSWER_MAX_CHARS:
        return text[:ANSWER_MAX_CHARS], True, ANSWER_MAX_CHARS, original
    return text or "", False, original, original


_clients: dict[str, "GLMClient"] = {}


def get_llm(provider: str | None = None) -> "GLMClient":
    pid = config.resolve_provider(provider)
    client = _clients.get(pid)
    if client is None:
        client = GLMClient(provider=pid)
        _clients[pid] = client
    return client


class GLMClient:
    def __init__(self, provider: str | None = None) -> None:
        self.provider = config.resolve_provider(provider)
        info = config.PROVIDERS[self.provider]
        api_key = str(info["api_key"])
        if not api_key or api_key == "your_api_key_here":
            raise ValueError(f"请在 .env 中配置有效的 {self.provider.upper()} API Key")
        self.client = OpenAI(
            api_key=api_key,
            base_url=str(info["base_url"]),
            timeout=120.0,
        )
        self.model = str(info["model"])
        self.label = str(info["label"])
        self.reasoning_effort = str(info.get("reasoning_effort") or "high")
        self.supports_thinking = bool(info.get("supports_thinking", True))

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        *,
        disable_thinking: bool = True,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout

        if self.supports_thinking:
            extra_body: dict = {}
            if disable_thinking:
                extra_body["thinking"] = {"type": "disabled"}
            else:
                extra_body["thinking"] = {"type": "enabled"}
                effort = reasoning_effort or self.reasoning_effort
                if effort:
                    extra_body["reasoning_effort"] = effort
            kwargs["extra_body"] = extra_body

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"调用 {self.label} 失败：{exc}") from exc

        message = response.choices[0].message
        content = (message.content or "").strip()
        if not content:
            reasoning = getattr(message, "reasoning_content", None) or ""
            content = str(reasoning).strip()
        if not content:
            raise RuntimeError(
                f"{self.label} 返回空内容，请检查模型名、API Key 或稍后重试"
            )
        return content

    def generate_pubmed_query(self, user_question: str) -> str:
        return self.chat(
            QUERY_SYSTEM_PROMPT,
            user_question,
            temperature=0.2,
            disable_thinking=True,
        )

    def answer_from_abstracts(self, question: str, abstracts_text: str) -> dict:
        text, truncated, uploaded_chars, original_chars = clip_answer_context(
            abstracts_text
        )
        paper_count = text.count("PMID-")
        note = (
            "（注意：输入已按长度上限截断，请基于仍保留的全部摘要作答。）\n"
            if truncated
            else ""
        )
        # 长文献放在前缀、问题放末尾，便于 GLM/DeepSeek 上下文缓存命中
        user_prompt = (
            f"以下是 PubMed Abstract 文献全文（txt，约 {uploaded_chars} 字符，"
            f"共 {paper_count} 篇摘要，以 PMID- 计数）。\n"
            f"{note}"
            f"请开启深度推理：系统梳理全部摘要后再回答，"
            f"按主题综合证据，尽量覆盖相关文献，避免只引用少数几篇。\n"
            f"引用格式必须为：（杂志名称，发表时间，PMID），"
            f"例如（BMC medical informatics and decision making，2025-03，40055694）。"
            f"请从每条记录的 JT-/TA-、DP-、PMID-、TI- 字段读取，勿遗漏。\n"
            f"回答最后必须写明：本次共分析了 {paper_count} 篇文献摘要。\n\n"
            f"{text}\n\n"
            f"----\n"
            f"用户问题：\n{question}"
        )
        answer = self.chat(
            ANSWER_SYSTEM_PROMPT,
            user_prompt,
            temperature=1.0,
            disable_thinking=False,
            max_tokens=16384,
            timeout=ANSWER_TIMEOUT_SEC,
        )
        return {
            "answer": answer,
            "truncated": truncated,
            "uploaded_chars": uploaded_chars,
            "original_chars": original_chars,
            "max_chars": ANSWER_MAX_CHARS,
            "paper_count": paper_count,
        }

    def translate_to_chinese(self, text: str, *, context: str = "") -> str:
        system = (
            "你是专业的英译中学术翻译。请将用户提供的文献片段译为准确、流畅的中文。"
            "保留专业术语（必要时括号保留英文原词）。不要添加原文没有的内容，不要解释。"
            "仅输出译文。"
        )
        user = text
        if context:
            user = f"【上下文标题】{context}\n\n【待译正文】\n{text}"
        if len(user) > 20000:
            user = user[:20000] + "\n…[已截断]"
        return self.chat(system, user, temperature=0.2, disable_thinking=True)

    def answer_from_pdf_text(self, question: str, pdf_text: str, *, filename: str = "") -> dict:
        text, truncated, uploaded_chars, original_chars = clip_answer_context(pdf_text)
        source = filename or "当前 PDF"
        note = (
            "（注意：全文已按长度上限截断，请基于仍保留的文本作答。）\n"
            if truncated
            else ""
        )
        # 全文在前、问题在后，便于 API 前缀缓存
        user_prompt = (
            f"文献文件：{source}\n"
            f"{note}"
            f"以下是该 PDF 的可提取文本（约 {uploaded_chars} 字符）：\n{text}\n\n"
            f"----\n"
            f"用户问题：\n{question}"
        )
        answer = self.chat(
            STUDY_QA_SYSTEM_PROMPT,
            user_prompt,
            temperature=1.0,
            disable_thinking=False,
            max_tokens=16384,
            timeout=ANSWER_TIMEOUT_SEC,
        )
        return {
            "answer": answer,
            "truncated": truncated,
            "uploaded_chars": uploaded_chars,
            "original_chars": original_chars,
            "max_chars": ANSWER_MAX_CHARS,
        }

    def deep_think_from_pdfs(self, prompt: str, corpus_text: str) -> dict:
        text, truncated, uploaded_chars, original_chars = clip_answer_context(corpus_text)
        note = (
            "（注意：输入已按长度上限截断，请基于仍保留的全部文献文本作答。）\n"
            if truncated
            else ""
        )
        user_prompt = (
            f"以下是用户选中的若干篇 PDF 文献全文（可提取文本，约 {uploaded_chars} 字符）。\n"
            f"{note}"
            f"请开启深度推理：先系统梳理、分析与归纳全部文献，再严格按用户思路产出"
            f"具体、可执行、完整的研究计划或文章初稿。"
            f"正文关键论断必须用（文献文件名）标明引文，文末必须给出「参考文献 / 引文」列表。\n\n"
            f"{text}\n\n"
            f"----\n"
            f"用户思路 / 要求：\n{prompt}"
        )
        answer = self.chat(
            DEEP_THINK_SYSTEM_PROMPT,
            user_prompt,
            temperature=1.0,
            disable_thinking=False,
            max_tokens=16384,
            timeout=ANSWER_TIMEOUT_SEC,
        )
        return {
            "answer": answer,
            "truncated": truncated,
            "uploaded_chars": uploaded_chars,
            "original_chars": original_chars,
            "max_chars": ANSWER_MAX_CHARS,
        }
