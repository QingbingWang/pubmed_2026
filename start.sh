#!/usr/bin/env bash
# PubMed 本地语义检索 — macOS / Linux 一键启动
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "========================================"
echo "  PubMed 本地系统 - 一键启动 (macOS)"
echo "========================================"
echo

# ---- 选择 Python 3.10+ ----
PYEXE=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      PYEXE="$(command -v "$cand")"
      break
    fi
  fi
done

if [[ -z "$PYEXE" ]]; then
  echo "[错误] 未找到 Python 3.10+。"
  echo "      可用 Homebrew 安装: brew install python@3.12"
  exit 1
fi

echo "[信息] 使用 Python: $PYEXE ($("$PYEXE" --version 2>&1))"

# ---- 虚拟环境 ----
VENV_PY="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "[信息] 虚拟环境缺失，正在创建 .venv ..."
  rm -rf "$ROOT/.venv"
  "$PYEXE" -m venv "$ROOT/.venv"
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "[错误] 虚拟环境创建后仍找不到 .venv/bin/python"
  exit 1
fi

echo "[信息] 检查并安装依赖..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r "$ROOT/requirements.txt"

# ---- .env ----
if [[ ! -f "$ROOT/.env" ]]; then
  if [[ -f "$ROOT/.env.example" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "[提示] 已从 .env.example 生成 .env，请填入 API Key 后重新运行。"
  else
    echo "[警告] 未找到 .env，请自行创建并配置 API Key。"
  fi
fi

HOST="$(grep -E '^FLASK_HOST=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
PORT="$(grep -E '^FLASK_PORT=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"
URL="http://${HOST}:${PORT}"

echo
echo "[启动] $URL"
echo "       Ctrl+C 停止服务"
echo

# 延迟打开浏览器，避免服务尚未就绪
(
  sleep 1.2
  if command -v open >/dev/null 2>&1; then
    open "$URL" || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" || true
  fi
) &

exec "$VENV_PY" "$ROOT/app.py"
