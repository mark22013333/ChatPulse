#!/usr/bin/env bash
set -e

# 單群摘要的快捷腳本。
#
# 用法範例：
#   ./scripts/run_summary.sh "0.暫存" 100
#   ./scripts/run_summary.sh "1.BU2-PG" 50 --style technical
#   ./scripts/run_summary.sh "1.BU2-PG" --style action_only
#
# 第 1 個參數是群組名稱關鍵字；第 2 個參數若是數字就當作則數；
# 其餘參數原封不動傳給 summarizer.py（例如 --style、--post）。
#
# 注意：不加 --post 就只會在終端輸出，不會推播回群組（規格 5.4）。

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=./_venv.sh
source "$SCRIPT_DIR/_venv.sh"

ensure_venv

# 第 1 個參數若是旗標（-h、--style…）就不當成群組名稱，直接原樣往下傳
SPACE="0.暫存"
if [ $# -gt 0 ] && [[ "$1" != -* ]]; then
    SPACE="$1"
    shift
fi

# 沒指定則數就不傳 --count，讓 core/config.py 的 LIMIT_DEFAULT 當唯一預設值
COUNT_ARGS=()
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then
    COUNT_ARGS=(--count "$1")
    shift
fi

exec "$VENV_PYTHON" "$PROJECT_DIR/mcp_app/summarizer.py" \
    --space "$SPACE" "${COUNT_ARGS[@]}" "$@"
