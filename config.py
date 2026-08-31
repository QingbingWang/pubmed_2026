"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
ABSTRACTS_DIR = DATA_DIR / "abstracts"
ABSTRACTS_DIR.mkdir(parents=True, exist_ok=True)
PDFS_DIR = DATA_DIR / "pdfs"
PDFS_DIR.mkdir(parents=True, exist_ok=True)
PDFS_WORKSPACE_DIR = DATA_DIR / "pdfs_workspace"
PDFS_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
STUDY_QA_DIR = DATA_DIR / "study_qa"
STUDY_QA_DIR.mkdir(parents=True, exist_ok=True)
ABSTRACT_QA_DIR = DATA_DIR / "abstract_qa"
ABSTRACT_QA_DIR.mkdir(parents=True, exist_ok=True)

GLM_API_KEY = os.getenv("GLM_API_KEY", "").strip()
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/").strip()
GLM_MODEL = os.getenv("GLM_MODEL", "glm-5.2").strip()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
).strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip()

# 默认提供方：glm | deepseek
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "glm").strip().lower() or "glm"

NCBI_API_KEY = os.getenv("NCBI_API_KEY", "").strip()
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "").strip()
PUBMED_MAX_RESULTS = int(os.getenv("PUBMED_MAX_RESULTS", "1500"))

FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

# Notion 导出（可选）
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28").strip() or "2022-06-28"


def notion_configured() -> bool:
    return _key_ok(NOTION_API_KEY) and bool(NOTION_DATABASE_ID)

PROVIDERS: dict[str, dict] = {
    "glm": {
        "id": "glm",
        "label": "GLM 5.2",
        "api_key": GLM_API_KEY,
        "base_url": GLM_BASE_URL,
        "model": GLM_MODEL,
        # 智谱深度推理
        "reasoning_effort": "max",
        "supports_thinking": True,
    },
    "deepseek": {
        "id": "deepseek",
        "label": "DeepSeek",
        "api_key": DEEPSEEK_API_KEY,
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_MODEL,
        # DeepSeek V4：reasoning_effort 取 high
        "reasoning_effort": "high",
        "supports_thinking": True,
    },
}


def _key_ok(key: str) -> bool:
    return bool(key) and key != "your_api_key_here"


def provider_configured(provider_id: str) -> bool:
    info = PROVIDERS.get(provider_id)
    return bool(info and _key_ok(str(info.get("api_key") or "")))


def resolve_provider(provider_id: str | None = None) -> str:
    """Pick a usable provider id; raise if none configured."""
    pid = (provider_id or LLM_PROVIDER or "glm").strip().lower()
    if pid not in PROVIDERS:
        raise ValueError(f"未知大模型提供方: {provider_id}")
    if provider_configured(pid):
        return pid
    # 请求的未配置时，回退到任一已配置的
    for candidate in ("glm", "deepseek"):
        if provider_configured(candidate):
            return candidate
    raise ValueError("请在 .env 中配置 GLM_API_KEY 或 DEEPSEEK_API_KEY")


def list_providers() -> list[dict]:
    items = []
    for pid, info in PROVIDERS.items():
        items.append(
            {
                "id": pid,
                "label": info["label"],
                "model": info["model"],
                "configured": provider_configured(pid),
            }
        )
    return items
