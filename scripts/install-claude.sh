#!/usr/bin/env bash
set -e

# 顏色定義
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN} 🚀 Google Chat MCP - Claude Code 一鍵安裝器 ${NC}"
echo -e "${CYAN}======================================================${NC}"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=./_venv.sh
source "$SCRIPT_DIR/_venv.sh"

MCP_SCRIPT="$PROJECT_DIR/mcp_app/mcp_server.py"

# 1. 準備執行環境（有 .venv 就用，沒有就用 uv 依 requirements.txt 建立）
ensure_venv
echo -e "${GREEN}✅ 執行環境就緒 (python: $VENV_PYTHON)${NC}"

# 2. 檢查 client_secret.json
SECRET_FILE="$PROJECT_DIR/config/client_secret.json"
if [ ! -f "$SECRET_FILE" ]; then
    echo -e "${RED}❌ 找不到憑證：$SECRET_FILE${NC}"
    echo -e "👉 請先向專案管理員索取 client_secret.json 並放置於上述路徑後再執行此指令！"
    exit 1
fi
echo -e "${GREEN}✅ 憑證檔案已就緒${NC}"

# 3. 提醒 Gemini API key（規格 3.3：缺這個變數 MCP server 啟動就會失敗）
if [ -z "${GOOGLE_API_KEY:-}" ]; then
    echo -e "${YELLOW}⚠️ 目前 shell 沒有 GOOGLE_API_KEY 環境變數。${NC}"
    echo -e "   Claude Code 由終端啟動會繼承 shell 環境，請先寫進 ~/.zshrc："
    echo -e "     export GOOGLE_API_KEY='你的 Gemini API key'"
    echo -e "   Claude Desktop 是 GUI 程式不讀 ~/.zshrc，需在 MCP 設定的 env 區塊填入。"
else
    echo -e "${GREEN}✅ 已偵測到 GOOGLE_API_KEY 環境變數${NC}"
fi

# 4. 執行 Google 帳號授權
echo -e "${CYAN}🔑 檢查個人授權狀態...${NC}"
"$VENV_PYTHON" "$PROJECT_DIR/mcp_app/setup_wizard.py"

# 5. 一鍵註冊至 Claude Code
echo -e "\n${CYAN}📦 正在將 Google Chat MCP 自動註冊進 Claude Code...${NC}"

if command -v claude &> /dev/null; then
    # 先移除既有同名（若存在）避免衝突
    claude mcp remove google-chat 2>/dev/null || true

    # 使用官方指令註冊 (傳入 scope user 讓任何專案都可用)
    claude mcp add --scope user google-chat -- "$VENV_PYTHON" "$MCP_SCRIPT"

    echo -e "\n${GREEN}🎉 安裝大功告成！${NC}"
    echo -e "${CYAN}現在開啟 Claude Code，輸入 /mcp 或直接發問：${NC}"
    echo -e "  💬 \"幫我列出 Google Chat 所有的群組\""
    echo -e "  💬 \"摘要 1.BU2-PG 最近 100 筆對話\""
    echo -e "  💬 \"用 technical 風格摘要 1.BU2-PG 最近 200 筆對話\""
else
    echo -e "${YELLOW}⚠️ 系統 PATH 中未找到 'claude' CLI 指令。${NC}"
    echo -e "   請改用安裝精靈印出的 JSON 片段，手動寫入 ~/.claude.json。"
fi

echo -e "${CYAN}======================================================${NC}"
