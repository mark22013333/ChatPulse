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

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
UV_BIN=$(which uv || echo "$HOME/.local/bin/uv")
MCP_SCRIPT="$PROJECT_DIR/mcp_app/mcp_server.py"

# 1. 檢查 uv
if [ ! -f "$UV_BIN" ] && ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}⚠️ 未偵測到 uv，正在為您安裝 uv...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV_BIN="$HOME/.local/bin/uv"
fi
echo -e "${GREEN}✅ 執行環境就緒 (uv: $UV_BIN)${NC}"

# 2. 檢查 client_secret.json
SECRET_FILE="$PROJECT_DIR/config/client_secret.json"
if [ ! -f "$SECRET_FILE" ]; then
    echo -e "${RED}❌ 找不到憑證：$SECRET_FILE${NC}"
    echo -e "👉 請先向專案管理員索取 client_secret.json 並放置於上述路徑後再執行此指令！"
    exit 1
fi
echo -e "${GREEN}✅ 憑證檔案已就緒${NC}"

# 3. 執行 Google 帳號授權
echo -e "${CYAN}🔑 檢查個人授權狀態...${NC}"
"$UV_BIN" run --with google-api-python-client --with google-auth-oauthlib --with google-auth-httplib2 --with requests \
  python3 "$PROJECT_DIR/mcp_app/setup_wizard.py"

# 4. 一鍵註冊至 Claude Code
echo -e "\n${CYAN}📦 正在將 Google Chat MCP 自動註冊進 Claude Code...${NC}"

if command -v claude &> /dev/null; then
    # 先移除既有同名（若存在）避免衝突
    claude mcp remove google-chat 2>/dev/null || true

    # 使用官方指令註冊 (傳入 scope user 讓任何專案都可用)
    claude mcp add --scope user google-chat -- "$UV_BIN" run \
      --with mcp \
      --with google-api-python-client \
      --with google-auth-oauthlib \
      --with google-auth-httplib2 \
      --with requests \
      python3 "$MCP_SCRIPT"

    echo -e "\n${GREEN}🎉 安裝大功告成！${NC}"
    echo -e "${CYAN}現在開啟 Claude Code，輸入 /mcp 或直接發問：${NC}"
    echo -e "  💬 \"幫我列出 Google Chat 所有的群組\""
    echo -e "  💬 \"摘要 1.BU2-PG 最近 100 筆對話\""
else
    echo -e "${YELLOW}⚠️ 系統 PATH 中未找到 'claude' CLI 指令，已自動將設定寫入 ~/.claude.json${NC}"
fi

echo -e "${CYAN}======================================================${NC}"
