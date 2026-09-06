#!/usr/bin/env bash
# ChatPulse Web 儀表板啟動器（macOS / Linux）
#
# 這支腳本刻意很薄：實際流程在 scripts/webapp.py，Windows 跑的是同一份。
#
# 為什麼變薄——這裡原本自己寫了一套「檢查 dist → npm install → npm run build」，
# 而 Windows 那條路徑（onboard.py 的 if IS_WINDOWS 分支）只有一行 uvicorn。
# 結果同事在 Windows 上一啟動就撞見「找不到前端建置產物」，維護者在 macOS 上
# 怎麼試都是好的。同一個專案裡的 if/else 兩側，就足以讓邏輯漂移。
#
# 用法：
#   ./scripts/start-web.sh          啟動儀表板
#   ./scripts/start-web.sh --dev    啟動並開啟自動重載（改後端程式碼會自動重啟）
#
# 這個入口保留給習慣它的人；日常建議直接用 ./chatpulse.sh web，
# 兩者跑的是同一段程式碼。

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=./_venv.sh
source "$SCRIPT_DIR/_venv.sh"

ensure_venv

exec "$VENV_PYTHON" "$SCRIPT_DIR/onboard.py" web "$@"
