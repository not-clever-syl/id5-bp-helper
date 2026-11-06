#!/usr/bin/env bash
# IVL 2026 秋季赛 · 求生方 BP 辅助工具 —— Linux / macOS 启动脚本
#
# 用法：
#   ./启动.sh            启动服务（默认 8766 端口），自动打开浏览器
#   ./启动.sh 8080       指定端口
#   ./启动.sh 8766 --no-browser   不自动打开浏览器
#
# 首次使用可能需要先给执行权限：
#   chmod +x 启动.sh

set -u

cd "$(dirname "$0")" || exit 1

PORT=8766
NO_BROWSER=""
for arg in "$@"; do
    case "$arg" in
        --no-browser) NO_BROWSER="--no-browser" ;;
        [0-9]*) PORT="$arg" ;;
    esac
done

echo
echo "  ============================================"
echo "    IVL 2026 秋季赛 · 求生方 BP 辅助工具"
echo "  ============================================"
echo

# ── 找 Python ──
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
            PY="$cand"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "  [X] 没有找到 Python 3.9 或更高版本"
    echo
    echo "  安装方式："
    echo "    macOS ：brew install python3"
    echo "            或从 https://www.python.org/downloads/ 下载安装"
    echo "    Debian/Ubuntu ：sudo apt install python3"
    echo "    Fedora/RHEL   ：sudo dnf install python3"
    echo
    exit 1
fi

echo "  使用 Python：$($PY --version 2>&1)"
echo

echo "  正在启动服务，浏览器会自动打开..."
echo "  按 Ctrl+C 停止服务"
echo

if [ -n "$NO_BROWSER" ]; then
    exec "$PY" scripts/serve.py "$PORT" --no-browser
else
    exec "$PY" scripts/serve.py "$PORT"
fi
