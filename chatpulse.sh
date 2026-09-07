#!/usr/bin/env bash
# ChatPulse 啟動器（macOS / Linux）
#
# 這個檔案刻意很薄：引導流程的邏輯全部在 scripts/onboard.py。
# 理由是那份邏輯要與 Windows 的 chatpulse.bat 共用——各寫一份必然漂移，
# 而 Windows 那份的坑，用 macOS 的維護者永遠踩不到。
# 薄殼只負責一件事：找到一個能跑的 Python，然後把棒子交出去。
#
# 用法：
#   ./chatpulse.sh          完整安裝引導（可重複執行）
#   ./chatpulse.sh check    只檢查安裝狀態（含 Sepia 潤稿規則是否隨專案下來）
#   ./chatpulse.sh auth     只重新做 Google 授權
#   ./chatpulse.sh mcp      只重新註冊 Claude Code 的 MCP 入口
#   ./chatpulse.sh web      只啟動 Web 儀表板
#
# 注意：Sepia 潤稿規則不需要安裝——它以純文字 vendored 在 core/polishers/sepia_rules/，
# 不是要另外裝的 Claude Code skill（ADR-0007）。check 只確認檔案在不在。

set -uo pipefail

BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ONBOARD="$BASE_DIR/scripts/onboard.py"

# 1) 專案自己的環境優先——它一定是對的版本，也裝好了套件
VENV_PYTHON="$BASE_DIR/.venv/bin/python"
if [ -x "$VENV_PYTHON" ]; then
    exec "$VENV_PYTHON" "$ONBOARD" "$@"
fi

# 2) 還沒建環境時，找一個系統 Python 來跑引導（它會負責建 .venv）
for candidate in python3.12 python3.13 python3 python; do
    BIN="$(command -v "$candidate" 2>/dev/null)" || continue
    # 3.10 以下跑不動這個專案，先擋掉再說，不要讓人卡在看不懂的語法錯誤
    if "$BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        exec "$BIN" "$ONBOARD" "$@"
    fi
done

echo ""
echo "  ✗ 找不到可用的 Python（需要 3.10 以上，建議 3.12）"
echo ""
echo "    macOS：brew install python@3.12"
echo "    Ubuntu/Debian：sudo apt install python3.12"
echo "    或到 https://www.python.org/downloads/ 下載安裝"
echo ""
echo "    裝好之後重新執行：./chatpulse.sh"
echo ""
exit 1
