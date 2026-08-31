"""Local Chrome web app for PubMed semantic search + GLM Q&A."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

import config
from services.pipeline import Pipeline

app = Flask(__name__)
pipeline: Pipeline | None = None


def get_pipeline() -> Pipeline:
    global pipeline
    if pipeline is None:
        pipeline = Pipeline()
    return pipeline


def _provider_from_payload(data: dict | None = None) -> str | None:
    raw = ""
    if data:
        raw = str(data.get("provider") or "").strip()
    if not raw:
        raw = str(request.args.get("provider") or "").strip()
    return raw or None


def _safe_abstract_path(name_or_path: str) -> Path | None:
    """Resolve a path only if it is inside ABSTRACTS_DIR."""
    abstracts_root = config.ABSTRACTS_DIR.resolve()
    candidate = Path(name_or_path)
    if not candidate.is_absolute():
        candidate = abstracts_root / candidate.name
    resolved = candidate.resolve()
    if abstracts_root not in resolved.parents and resolved.parent != abstracts_root:
        return None
    if resolved.suffix.lower() != ".txt":
        return None
    return resolved


def _parse_file_meta(path: Path) -> dict:
    keywords: list[str] = []
    query = ""
    count = None
    retrieved = ""
    try:
        # only read header portion；忽略非 utf-8 临时文件
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i > 30 or line.startswith("=" * 10):
                    break
                if line.startswith("Keywords:"):
                    raw = line[len("Keywords:") :].strip()
                    keywords = [k.strip() for k in raw.split(",") if k.strip()][:2]
                elif line.startswith("Query:"):
                    query = line[len("Query:") :].strip()
                elif line.startswith("Count:"):
                    try:
                        count = int(line[len("Count:") :].strip())
                    except ValueError:
                        count = None
                elif line.startswith("Retrieved:"):
                    retrieved = line[len("Retrieved:") :].strip()
    except OSError:
        pass

    # fallback keywords from filename: Kw1_Kw2_YYYYMMDD.txt
    if not keywords:
        tokens = path.stem.split("_")
        date_like = [t for t in tokens if len(t) == 8 and t.isdigit()]
        if date_like:
            idx = tokens.index(date_like[0])
            keywords = [t for t in tokens[:idx] if t and t != "pubmed"][:2]
        else:
            keywords = [t for t in tokens if not t.isdigit()][:2]

    label = " · ".join(keywords) if keywords else path.stem
    mtime = path.stat().st_mtime
    date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    return {
        "filename": path.name,
        "filepath": str(path),
        "size": path.stat().st_size,
        "mtime": mtime,
        "date": date_str,
        "keywords": keywords,
        "label": label,
        "query": query,
        "count": count,
        "retrieved": retrieved,
    }


def _is_abstract_txt(path: Path) -> bool:
    """Skip gitkeep / Word lock files (~$...) / hidden junk."""
    name = path.name
    if not path.is_file():
        return False
    if name == ".gitkeep":
        return False
    if name.startswith("~$") or name.startswith("."):
        return False
    if not name.lower().endswith(".txt"):
        return False
    return True


def _strip_abstract_header(text: str) -> str:
    """Drop export header before the ===== divider; keep abstract body only."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip().startswith("=" * 10) and set(line.strip()) <= {"="}:
            return "".join(lines[i + 1 :]).lstrip("\n")
    return text


def _list_abstract_files() -> list[dict]:
    files = [p for p in config.ABSTRACTS_DIR.glob("*.txt") if _is_abstract_txt(p)]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    items: list[dict] = []
    for p in files:
        try:
            items.append(_parse_file_meta(p))
        except Exception:  # noqa: BLE001 — 单个坏文件不影响列表
            continue
    return items


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    providers = config.list_providers()
    any_ok = any(p["configured"] for p in providers)
    try:
        default_id = config.resolve_provider(config.LLM_PROVIDER)
    except ValueError:
        default_id = config.LLM_PROVIDER if config.LLM_PROVIDER in config.PROVIDERS else "glm"
    default = next((p for p in providers if p["id"] == default_id), providers[0])
    return jsonify(
        {
            "ok": True,
            "glm_configured": any_ok,
            "configured": any_ok,
            "model": default["model"],
            "provider": default["id"],
            "providers": providers,
            "default_provider": default_id,
            "max_results": config.PUBMED_MAX_RESULTS,
            "notion_configured": config.notion_configured(),
        }
    )


@app.post("/api/build-query")
def api_build_query():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请输入问题或检索需求"}), 400
    try:
        result = get_pipeline().build_query(
            question, provider=_provider_from_payload(data)
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/search")
def api_search():
    data = request.get_json(silent=True) or {}
    pubmed_query = (data.get("pubmed_query") or "").strip()
    if not pubmed_query:
        return jsonify({"ok": False, "error": "缺少 PubMed 检索式"}), 400
    keywords = data.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    max_results = data.get("max_results")
    try:
        max_results_int = int(max_results) if max_results else None
    except (TypeError, ValueError):
        max_results_int = None
    try:
        result = get_pipeline().search_literature(
            pubmed_query,
            max_results=max_results_int,
            keywords=list(keywords)[:2],
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/answer")
def api_answer():
    from services import context_cache

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请输入问题"}), 400

    abstracts_text = data.get("abstracts_text") or ""
    filepaths = data.get("filepaths") or []
    filepath = (data.get("filepath") or "").strip()
    if filepath and filepath not in filepaths:
        filepaths = [filepath, *filepaths]

    paths: list[Path] = []
    used_files: list[str] = []
    for item in filepaths:
        path = _safe_abstract_path(str(item))
        if path is None or not path.exists():
            return jsonify({"ok": False, "error": f"非法或缺失文件: {item}"}), 400
        paths.append(path)
        used_files.append(path.name)

    cache_hit = False
    if paths:
        # 固定文件顺序，保证本地缓存与 API 前缀缓存稳定
        paired = sorted(zip(paths, used_files), key=lambda x: x[1])
        paths = [p for p, _ in paired]
        used_files = [n for _, n in paired]
        cache_key = "abstracts:" + "|".join(used_files)

        def _load() -> str:
            chunks: list[str] = []
            for path, name in zip(paths, used_files):
                raw = path.read_text(encoding="utf-8", errors="ignore")
                body = _strip_abstract_header(raw)
                if not body.strip():
                    raise ValueError(f"摘要正文为空: {name}")
                chunks.append(f"===== 文献集: {name} =====\n{body}")
            return "\n\n".join(chunks)

        try:
            abstracts_text, cache_hit = context_cache.get_or_load(
                cache_key, paths, _load
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"ok": False, "error": f"读取摘要文件失败: {exc}"}), 400
    elif abstracts_text.strip():
        abstracts_text = _strip_abstract_header(abstracts_text)

    if not abstracts_text.strip():
        return jsonify({"ok": False, "error": "请先勾选近期检索结果"}), 400

    try:
        result = get_pipeline().answer(
            question, abstracts_text, provider=_provider_from_payload(data)
        )
        return jsonify(
            {
                "ok": True,
                "answer": result.get("answer") or "",
                "used_files": used_files,
                "context_cache_hit": cache_hit,
                "truncated": bool(result.get("truncated")),
                "uploaded_chars": int(result.get("uploaded_chars") or 0),
                "original_chars": int(result.get("original_chars") or 0),
                "max_chars": int(result.get("max_chars") or 0),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/files")
def api_files():
    try:
        limit = request.args.get("limit", type=int)
        items = _list_abstract_files()
        total = len(items)
        if limit is not None and limit > 0:
            items = items[:limit]
        return jsonify({"ok": True, "files": items, "total": total})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"读取文件列表失败: {exc}"}), 500


@app.get("/api/files/<path:filename>")
def api_download_file(filename: str):
    safe = Path(filename).name
    path = _safe_abstract_path(safe)
    if path is None or not path.exists():
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    return send_from_directory(config.ABSTRACTS_DIR, safe, as_attachment=False)


@app.post("/api/files/delete")
def api_delete_files():
    data = request.get_json(silent=True) or {}
    names = data.get("filenames") or data.get("filepaths") or []
    if not names:
        return jsonify({"ok": False, "error": "未指定要删除的文件"}), 400
    deleted: list[str] = []
    errors: list[str] = []
    for item in names:
        path = _safe_abstract_path(str(item))
        if path is None or not path.exists():
            errors.append(str(item))
            continue
        try:
            path.unlink()
            deleted.append(path.name)
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return jsonify({"ok": True, "deleted": deleted, "errors": errors})


# ---------- 全文学习 ----------

_study_folder: Path | None = None
_study_folder_label: str = ""


def _resolve_pdf_in_study_folder(filename: str) -> Path:
    global _study_folder
    folder = _study_folder or config.PDFS_WORKSPACE_DIR.resolve()
    safe = Path(filename).name
    path = (folder / safe).resolve()
    root = folder.resolve()
    if root not in path.parents and path.parent != root:
        raise ValueError("非法文件路径")
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("PDF 文件不存在")
    return path


def _safe_study_qa_store_name(filename: str) -> str:
    """Map PDF filename to a local JSON store name under study_qa/."""
    safe = Path(filename).name
    if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe:
        raise ValueError("非法文件名")
    if not safe.lower().endswith(".pdf"):
        raise ValueError("仅支持 PDF 文献的回答存档")
    # keep basename; sanitize characters unsafe on Windows
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", safe)
    return f"{cleaned}.json"


def _study_qa_path(filename: str) -> Path:
    return (config.STUDY_QA_DIR / _safe_study_qa_store_name(filename)).resolve()


def _load_study_qa(filename: str) -> dict:
    path = _study_qa_path(filename)
    if not path.is_file():
        return {"filename": Path(filename).name, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"filename": Path(filename).name, "items": []}
    if not isinstance(data, dict):
        return {"filename": Path(filename).name, "items": []}
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []
    return {
        "filename": data.get("filename") or Path(filename).name,
        "items": items,
    }


def _normalize_abstract_names(filenames: list | None) -> list[str]:
    names: list[str] = []
    for item in filenames or []:
        name = Path(str(item)).name
        if not name or name in {".", ".."}:
            continue
        if not name.lower().endswith(".txt"):
            raise ValueError(f"非法摘要文件: {name}")
        if name.startswith("~$") or name.startswith("."):
            raise ValueError(f"非法摘要文件: {name}")
        names.append(name)
    # 稳定排序，保证同一勾选集合对应同一存档
    return sorted(set(names))


def _abstract_qa_store_name(filenames: list[str]) -> str:
    names = _normalize_abstract_names(filenames)
    if not names:
        raise ValueError("请先勾选至少一个检索结果")
    joined = "__".join(names)
    if len(joined) > 160:
        import hashlib

        digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
        joined = f"set_{digest}_{len(names)}files"
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", joined)
    return f"{cleaned}.json"


def _abstract_qa_path(filenames: list[str]) -> Path:
    return (config.ABSTRACT_QA_DIR / _abstract_qa_store_name(filenames)).resolve()


def _load_abstract_qa(filenames: list[str]) -> dict:
    names = _normalize_abstract_names(filenames)
    path = _abstract_qa_path(names)
    if not path.is_file():
        return {"filenames": names, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"filenames": names, "items": []}
    if not isinstance(data, dict):
        return {"filenames": names, "items": []}
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []
    return {
        "filenames": data.get("filenames") or names,
        "items": items,
    }


@app.get("/api/abstract/saved-qa")
def api_abstract_saved_qa_get():
    """List saved Q&A for currently selected abstract TXT set."""
    files = request.args.getlist("files") or request.args.getlist("filenames")
    if not files:
        raw = (request.args.get("files") or request.args.get("filenames") or "").strip()
        if raw:
            files = [x.strip() for x in re.split(r"[,;|]", raw) if x.strip()]
    try:
        data = _load_abstract_qa(files)
        return jsonify({"ok": True, **data})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/abstract/saved-qa")
def api_abstract_saved_qa_save():
    """Append one abstract Q&A: filenames + question + answer."""
    data = request.get_json(silent=True) or {}
    filenames = data.get("filenames") or data.get("files") or data.get("filepaths") or []
    if isinstance(filenames, str):
        filenames = [filenames]
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "问题不能为空"}), 400
    if not answer:
        return jsonify({"ok": False, "error": "回答不能为空"}), 400
    try:
        names = _normalize_abstract_names(list(filenames))
        store = _load_abstract_qa(names)
        item = {
            "id": uuid.uuid4().hex,
            "filenames": names,
            "question": question,
            "answer": answer,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        items = list(store.get("items") or [])
        items.append(item)
        payload = {"filenames": names, "items": items}
        path = _abstract_qa_path(names)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return jsonify({"ok": True, "item": item, "items": items, "count": len(items)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": f"保存失败: {exc}"}), 500


@app.post("/api/study/upload")
def api_study_upload():
    """Receive PDFs from browser folder picker into local workspace."""
    global _study_folder, _study_folder_label
    from services.pdf_reader import list_pdfs

    label = (request.form.get("folder_name") or "已选文件夹").strip()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "未选择任何文件"}), 400

    workspace = config.PDFS_WORKSPACE_DIR.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    # 每次打开文件夹时清空旧工作区，避免混入上次文件
    for old in workspace.glob("*.pdf"):
        try:
            old.unlink()
        except OSError:
            pass

    saved: list[str] = []
    for f in files:
        if not f or not f.filename:
            continue
        # webkitdirectory 可能带相对路径 sub/a.pdf
        name = Path(f.filename.replace("\\", "/")).name
        if not name.lower().endswith(".pdf"):
            continue
        dest = workspace / name
        # 重名时加序号
        if dest.exists():
            stem, suf = dest.stem, dest.suffix
            n = 2
            while True:
                cand = workspace / f"{stem}_{n}{suf}"
                if not cand.exists():
                    dest = cand
                    break
                n += 1
        f.save(dest)
        saved.append(dest.name)

    if not saved:
        return jsonify({"ok": False, "error": "文件夹中没有 PDF 文件"}), 400

    _study_folder = workspace
    _study_folder_label = label
    pdfs = list_pdfs(workspace)
    return jsonify(
        {
            "ok": True,
            "folder": label,
            "pdfs": pdfs,
            "count": len(pdfs),
        }
    )


@app.get("/api/study/pdfs")
def api_study_pdfs():
    from services.pdf_reader import list_pdfs

    global _study_folder
    try:
        folder = _study_folder or config.PDFS_WORKSPACE_DIR.resolve()
        if not folder.is_dir():
            folder = config.PDFS_WORKSPACE_DIR.resolve()
        _study_folder = folder
        pdfs = list_pdfs(folder)
        return jsonify(
            {
                "ok": True,
                "folder": _study_folder_label or str(folder),
                "pdfs": pdfs,
                "count": len(pdfs),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/study/file/<path:filename>")
def api_study_file(filename: str):
    """Serve PDF binary for PDF.js viewer."""
    try:
        path = _resolve_pdf_in_study_folder(filename)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return send_from_directory(
        path.parent,
        path.name,
        mimetype="application/pdf",
        as_attachment=False,
        max_age=0,
    )


@app.get("/api/study/saved-qa/<path:filename>")
def api_study_saved_qa_get(filename: str):
    """List saved Q&A for a PDF (全文学习 only)."""
    try:
        data = _load_study_qa(filename)
        return jsonify({"ok": True, **data})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/study/saved-qa")
def api_study_saved_qa_save():
    """Append one Q&A record for a PDF: filename + question + answer."""
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not filename:
        return jsonify({"ok": False, "error": "缺少文献文件名"}), 400
    if not question:
        return jsonify({"ok": False, "error": "问题不能为空"}), 400
    if not answer:
        return jsonify({"ok": False, "error": "回答不能为空"}), 400
    try:
        store = _load_study_qa(filename)
        item = {
            "id": uuid.uuid4().hex,
            "filename": Path(filename).name,
            "question": question,
            "answer": answer,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        items = list(store.get("items") or [])
        items.append(item)
        payload = {
            "filename": Path(filename).name,
            "items": items,
        }
        path = _study_qa_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return jsonify({"ok": True, "item": item, "items": items, "count": len(items)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": f"保存失败: {exc}"}), 500


@app.post("/api/study/ask")
def api_study_ask():
    """Answer a question based on the currently opened PDF text."""
    from services import context_cache
    from services.pdf_reader import structure_pdf

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    filename = (data.get("filename") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请输入问题"}), 400
    if not filename:
        return jsonify({"ok": False, "error": "请先打开一篇 PDF 文献"}), 400
    try:
        path = _resolve_pdf_in_study_folder(filename)

        def _load() -> str:
            structured = structure_pdf(path)
            chunks: list[str] = []
            for block in structured.get("blocks") or []:
                chunks.append(block.get("text") or "")
            return "\n\n".join(c for c in chunks if c.strip())

        pdf_text, cache_hit = context_cache.get_or_load(
            f"pdf:{filename}", [path], _load
        )
        if not pdf_text.strip():
            return jsonify({"ok": False, "error": "该 PDF 无可提取文本（可能是扫描版）"}), 400
        result = get_pipeline().llm(_provider_from_payload(data)).answer_from_pdf_text(
            question, pdf_text, filename=filename
        )
        return jsonify(
            {
                "ok": True,
                "answer": result.get("answer") or "",
                "filename": filename,
                "context_cache_hit": cache_hit,
                "truncated": bool(result.get("truncated")),
                "uploaded_chars": int(result.get("uploaded_chars") or 0),
                "original_chars": int(result.get("original_chars") or 0),
                "max_chars": int(result.get("max_chars") or 0),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/study/translate")
def api_study_translate():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    context = (data.get("context") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "没有可翻译的内容"}), 400
    try:
        translation = get_pipeline().llm(
            _provider_from_payload(data)
        ).translate_to_chinese(text, context=context)
        return jsonify({"ok": True, "translation": translation})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/deep-think")
def api_deep_think():
    """Analyze selected PDFs and produce a research plan or draft from free-form prompt."""
    from services import context_cache
    from services.pdf_reader import structure_pdf

    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    filenames = data.get("filenames") or []
    if not prompt:
        return jsonify({"ok": False, "error": "请输入思路 / 要求"}), 400
    if not isinstance(filenames, list) or not filenames:
        return jsonify({"ok": False, "error": "请先勾选并发送至少一篇文献"}), 400

    # 固定顺序，保证本地缓存与 API 前缀缓存稳定
    names = sorted({Path(str(name)).name for name in filenames if str(name).strip()})
    if not names:
        return jsonify({"ok": False, "error": "请先勾选并发送至少一篇文献"}), 400

    paths: list[Path] = []
    used_files: list[str] = []
    chunks: list[str] = []
    cache_hits = 0
    try:
        for name in names:
            path = _resolve_pdf_in_study_folder(name)

            def _load(p: Path = path) -> str:
                structured = structure_pdf(p)
                parts: list[str] = []
                for block in structured.get("blocks") or []:
                    parts.append(block.get("text") or "")
                return "\n\n".join(c for c in parts if c.strip())

            pdf_text, hit = context_cache.get_or_load(f"pdf:{name}", [path], _load)
            if hit:
                cache_hits += 1
            if not pdf_text.strip():
                return jsonify(
                    {"ok": False, "error": f"该 PDF 无可提取文本（可能是扫描版）: {name}"}
                ), 400
            paths.append(path)
            used_files.append(name)
            chunks.append(f"===== 文献: {name} =====\n{pdf_text}")

        corpus = "\n\n".join(chunks)
        result = get_pipeline().llm(_provider_from_payload(data)).deep_think_from_pdfs(
            prompt, corpus
        )
        return jsonify(
            {
                "ok": True,
                "answer": result.get("answer") or "",
                "used_files": used_files,
                "context_cache_hit": cache_hits == len(used_files) and bool(used_files),
                "truncated": bool(result.get("truncated")),
                "uploaded_chars": int(result.get("uploaded_chars") or 0),
                "original_chars": int(result.get("original_chars") or 0),
                "max_chars": int(result.get("max_chars") or 0),
            }
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/notion/health")
def api_notion_health():
    return jsonify(
        {
            "ok": True,
            "configured": config.notion_configured(),
            "database_id": bool(config.NOTION_DATABASE_ID),
            "has_key": bool(
                config.NOTION_API_KEY and config.NOTION_API_KEY != "your_api_key_here"
            ),
        }
    )


@app.post("/api/notion/export")
def api_notion_export():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "标题（提问）不能为空"}), 400
    if not answer:
        return jsonify({"ok": False, "error": "回答内容不能为空"}), 400
    try:
        from services.notion_client import get_notion_client

        result = get_notion_client().create_qa_page(
            {
                "title": title,
                "question": title,
                "answer": answer,
                "summary": data.get("summary") or "",
                "learn_date": data.get("learn_date") or "",
                "source_name": data.get("source_name") or "",
                "source_type": data.get("source_type") or "",
                "tags": data.get("tags") or [],
                "status": data.get("status") or "",
                "importance": data.get("importance") or "",
                "url": data.get("url") or data.get("link") or "",
            }
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    print(f"打开 Chrome 访问: http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=True)
