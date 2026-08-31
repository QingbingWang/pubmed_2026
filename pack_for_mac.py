#!/usr/bin/env python3
"""Pack pumed_local for Mac migration (exclude Windows .venv and caches)."""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
STAMP = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

EXCLUDE_DIR_NAMES = {
    ".venv",
    "__pycache__",
    ".git",
    "dist",
    ".cursor",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
EXCLUDE_FILE_NAMES = {
    "_pack_log.txt",
    "dist_pack_stdout.txt",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip"}


def should_skip(path: Path) -> bool:
    rel_parts = path.relative_to(ROOT).parts
    if any(p in EXCLUDE_DIR_NAMES for p in rel_parts):
        return True
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    if path.name.startswith("pumed_local_mac_"):
        return True
    return False


def collect_files() -> list[Path]:
    files: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if should_skip(p):
            continue
        files.append(p)
    return sorted(files)


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / f"pumed_local_mac_{STAMP}.zip"
    files = collect_files()
    has_env = any(f.name == ".env" for f in files)

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arc = Path("pumed_local") / f.relative_to(ROOT)
            # normalize to forward slashes inside zip
            zf.write(f, arcname=arc.as_posix())

    size_mb = out.stat().st_size / (1024 * 1024)
    report = ROOT / "dist" / f"pack_report_{STAMP}.txt"
    lines = [
        f"output: {out}",
        f"files: {len(files)}",
        f"size_mb: {size_mb:.2f}",
        f"includes_.env: {has_env}",
        "",
        "included (top-level):",
    ]
    top = sorted({f.relative_to(ROOT).parts[0] for f in files})
    lines.extend(f"  - {name}" for name in top)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nreport: {report}")


if __name__ == "__main__":
    main()
