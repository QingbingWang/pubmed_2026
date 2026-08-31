"""In-process cache for abstract TXT / PDF text until the source file changes."""

from __future__ import annotations

from pathlib import Path

# key -> (signature, text)
_cache: dict[str, tuple[str, str]] = {}


def _sig_for_paths(paths: list[Path]) -> str:
    parts: list[str] = []
    for p in paths:
        try:
            st = p.stat()
            parts.append(f"{p.resolve()}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{p}:missing")
    return "|".join(parts)


def get_cached(key: str, signature: str) -> str | None:
    hit = _cache.get(key)
    if hit and hit[0] == signature:
        return hit[1]
    return None


def set_cached(key: str, signature: str, text: str) -> str:
    _cache[key] = (signature, text)
    return text


def get_or_load(key: str, paths: list[Path], loader) -> tuple[str, bool]:
    """Return (text, from_cache). loader() -> str when miss."""
    signature = _sig_for_paths(paths)
    cached = get_cached(key, signature)
    if cached is not None:
        return cached, True
    text = loader()
    set_cached(key, signature, text)
    return text, False


def clear() -> None:
    _cache.clear()
