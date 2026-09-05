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

# 2. 檢查 client_secret.json（config/ 不進版控，clone 後不存在，先幫忙建出來）
mkdir -p "$PROJECT_DIR/config"
SECRET_FILE="$PROJECT_DIR/config/client_secret.json"
if [ ! -f "$SECRET_FILE" ]; then
    echo -e "${RED}❌ 找不到憑證：$SECRET_FILE${NC}"
    echo -e "   config/ 目錄我已經幫你建好了，缺的是裡面的檔案。"
    echo -e "👉 請向專案管理員索取 client_secret.json，放進上述路徑後再執行一次。"
    echo -e "   （想自己在 GCP 建一份的話，見 SETUP_GUIDE.md 的 Q1）"
    exit 1
fi
echo -e "${GREEN}✅ 憑證檔案已就緒${NC}"

# 3. 檢查 AI 供應商（摘要與 Draft Reply 需要；只讀 Space 與訊息不需要）
#    判斷交給 core.providers，不要在這裡另外寫一套規則。
echo -e "${CYAN}🤖 檢查 AI 供應商...${NC}"
if ! "$VENV_PYTHON" -c "
import sys; sys.path.insert(0, '$PROJECT_DIR')
from core import providers
avail = [d for d in providers.describe_all() if d['available']]
if not avail:
    print('')
    print('⚠️  目前沒有任何可用的 AI 供應商：列群組與讀訊息不受影響，但摘要會失敗。')
    for d in providers.describe_all():
        print(f\"   - {d['name']}：{d['reason']}\")
    print('')
    print('   最省事的解法：裝好並登入 Claude Code（終端輸入 claude 能進對話即可），')
    print('   它會直接用你現有的訂閱，不需要任何 API key。')
    sys.exit(1)
print(f\"✅ AI 供應商可用：{'、'.join(d['name'] for d in avail)}（預設 {providers.default_name()}）\")
"; then
    echo -e "${YELLOW}   安裝會繼續，但摘要功能要等你補上供應商才能用。${NC}"
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
