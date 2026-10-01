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

# 检索项目根目录。每次新检索在此按 _template 新建一个项目：
#   {关键词}_{日期}/{关键词}_abstract_{日期}.txt
#   {关键词}_{日期}/_full_pdf/
_raw_projects = os.getenv("PROJECTS_DIR", "").strip().strip('"')
PROJECTS_DIR = Path(_raw_projects) if _raw_projects else (DATA_DIR / "projects")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
).strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip()

# 默认提供方：deepseek（已取消 GLM）
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").strip().lower() or "deepseek"
if LLM_PROVIDER == "glm":
    LLM_PROVIDER = "deepseek"

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
    pid = (provider_id or LLM_PROVIDER or "deepseek").strip().lower()
    if pid == "glm":
        pid = "deepseek"
    if pid not in PROVIDERS:
        raise ValueError(f"未知大模型提供方: {provider_id}")
    if provider_configured(pid):
        return pid
    for candidate in ("deepseek",):
        if provider_configured(candidate):
            return candidate
    raise ValueError("请在 .env 中配置 DEEPSEEK_API_KEY")


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
