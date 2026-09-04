#!/usr/bin/env bash
set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
UV_BIN=$(which uv || echo "$HOME/.local/bin/uv")

echo "======================================================"
echo " 🚀 正在啟動 ChatPulse - Google Chat 智慧儀表板服務... "
echo "======================================================"

# 確保依賴完整
"$UV_BIN" run \
  --with fastapi \
  --with uvicorn \
  --with google-api-python-client \
  --with google-auth-oauthlib \
  --with google-auth-httplib2 \
  --with requests \
  python3 -c "import fastapi, uvicorn; print('依賴套件正常！')"

echo ""
echo "🌐 儀表板服務運行於: http://localhost:8000"
echo "👉 正在為您開啟瀏覽器..."

# 背景開啟瀏覽器
(sleep 1.5 && open "http://localhost:8000" 2>/dev/null || true) &

# 啟動 FastAPI 服務
"$UV_BIN" run \
  --with fastapi \
  --with uvicorn \
  --with google-api-python-client \
  --with google-auth-oauthlib \
  --with google-auth-httplib2 \
  --with requests \
  uvicorn dashboard.api.server:app --host 127.0.0.1 --port 8000 --reload
