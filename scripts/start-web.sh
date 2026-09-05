#!/usr/bin/env bash
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=./_venv.sh
source "$SCRIPT_DIR/_venv.sh"

echo "======================================================"
echo " 🚀 正在啟動 ChatPulse - Google Chat 智慧儀表板服務... "
echo "======================================================"

# 有 .venv 就直接用；沒有才用 uv 依 requirements.txt 建一份
ensure_venv

# 確保依賴完整（版本已鎖定，這裡只做最後一道 import 檢查）
"$VENV_PYTHON" -c "import fastapi, uvicorn; print('✅ 依賴套件正常！')"

# 前端建置產物：沒有的話後端只會回一段提示，不會有畫面（規格 3.2 要求
# 建置產物交由 FastAPI 靜態托管），所以這裡幫忙建一次。
FRONTEND_DIR="$PROJECT_DIR/dashboard/frontend"
if [ ! -f "$FRONTEND_DIR/dist/index.html" ]; then
    echo ""
    echo "⚠️  找不到前端建置產物，正在建置（第一次會比較久）..."
    if command -v npm >/dev/null 2>&1; then
        if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
            npm --prefix "$FRONTEND_DIR" install
        fi
        npm --prefix "$FRONTEND_DIR" run build
        echo "✅ 前端建置完成"
    else
        echo "❌ 找不到 npm，無法建置前端。API 仍可使用，但沒有畫面。"
        echo "   裝好 Node.js 後執行：npm --prefix $FRONTEND_DIR install && npm --prefix $FRONTEND_DIR run build"
    fi
fi

if [ -z "${GOOGLE_API_KEY:-}" ]; then
    echo ""
    echo "⚠️  GOOGLE_API_KEY 未設定：Space 與訊息讀取不受影響，但摘要與 Draft Reply 會失敗。"
    echo "   設定方式：export GOOGLE_API_KEY='你的 Gemini API key'"
fi

echo ""
echo "🌐 儀表板服務運行於: http://localhost:8000"
echo "👉 正在為您開啟瀏覽器..."

# 背景開啟瀏覽器
(sleep 1.5 && open "http://localhost:8000" 2>/dev/null || true) &

# 啟動 FastAPI 服務（要在專案根目錄才找得到 dashboard 套件）
cd "$PROJECT_DIR"
exec "$VENV_BIN/uvicorn" dashboard.api.server:app --host 127.0.0.1 --port 8000 --reload
