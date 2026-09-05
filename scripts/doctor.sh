#!/usr/bin/env bash
# ChatPulse 安裝自我檢查
#
# 給剛裝好的同事用：跑這一行就知道自己哪裡還沒弄好，以及下一步該做什麼。
# 每一項都印「怎麼修」，不要只印失敗——看到紅字卻不知道下一步等於沒檢查。
#
# 刻意不用 set -e：這支腳本的目的就是把**所有**問題一次列出來，
# 第一項失敗就中止的話，同事得來回跑五次才問得完。

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

FAILED=0
WARNED=0

ok()   { echo -e "  ${GREEN}✅ $1${NC}"; }
bad()  { echo -e "  ${RED}❌ $1${NC}"; echo -e "     ${CYAN}→ $2${NC}"; FAILED=$((FAILED+1)); }
warn() { echo -e "  ${YELLOW}⚠️  $1${NC}"; echo -e "     ${CYAN}→ $2${NC}"; WARNED=$((WARNED+1)); }

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN} 🩺 ChatPulse 安裝檢查${NC}"
echo -e "${CYAN}======================================================${NC}"
echo -e "專案位置：$PROJECT_DIR\n"

# ── 1. Python 執行環境 ─────────────────────────────
echo "【1】Python 執行環境"
if [ -x "$VENV_PYTHON" ]; then
    ok "虛擬環境存在（$("$VENV_PYTHON" --version 2>&1)）"
    if "$VENV_PYTHON" -c "import fastapi, uvicorn, mcp, cryptography" 2>/dev/null; then
        ok "必要套件都裝好了"
    else
        MISSING=$("$VENV_PYTHON" -c "
import importlib
for m in ['fastapi','uvicorn','mcp','cryptography','googleapiclient','requests']:
    try: importlib.import_module(m)
    except ImportError: print(m, end=' ')
" 2>/dev/null)
        bad "套件缺漏：$MISSING" "在專案目錄執行：uv pip install --python .venv/bin/python -r requirements.txt"
    fi
else
    bad "找不到虛擬環境 .venv" "執行 ./scripts/install-claude.sh 或 ./scripts/start-web.sh，兩者都會自動建立"
fi

# ── 2. Google OAuth 憑證 ───────────────────────────
echo -e "\n【2】Google 憑證"
if [ -f "$PROJECT_DIR/config/client_secret.json" ]; then
    ok "client_secret.json 已就位"
else
    bad "找不到 config/client_secret.json" "向專案維護者索取這個檔案，放進 config/ 目錄（沒有該目錄就先 mkdir config）"
fi

TOKEN_FILE="$PROJECT_DIR/config/google_chat_token.json"
if [ -f "$TOKEN_FILE" ] && [ -x "$VENV_PYTHON" ]; then
    SCOPE_CHECK=$("$VENV_PYTHON" -c "
import json, sys
sys.path.insert(0, '$PROJECT_DIR')
from core import config as cfg
try:
    have = set(json.load(open('$TOKEN_FILE')).get('scopes') or [])
except Exception as exc:
    print('BROKEN'); sys.exit()
missing = set(cfg.DASHBOARD_SCOPES) - have
print('FULL' if not missing else ('CHAT_ONLY' if not (set(cfg.CHAT_SCOPES) - have) else 'PARTIAL'))
" 2>/dev/null)
    case "$SCOPE_CHECK" in
        FULL)      ok "個人 Token 已授權，權限完整（MCP 與儀表板都能用）" ;;
        CHAT_ONLY) warn "個人 Token 只有三個 chat 權限，Web 儀表板不能用" \
                        "重跑 ./scripts/setup.sh 重新授權一次即可（MCP 目前可正常使用）" ;;
        PARTIAL)   bad "個人 Token 權限不足" "重跑 ./scripts/setup.sh 重新授權" ;;
        *)         bad "個人 Token 檔案讀不出來（可能已損壞）" "刪掉 config/google_chat_token.json 後重跑 ./scripts/setup.sh" ;;
    esac
elif [ -x "$VENV_PYTHON" ]; then
    bad "還沒完成 Google 授權" "執行 ./scripts/setup.sh，在瀏覽器選你的公司帳號並同意"
fi

# ── 3. AI 供應商 ───────────────────────────────────
echo -e "\n【3】AI 供應商（摘要與 Draft Reply 需要）"
if [ -x "$VENV_PYTHON" ]; then
    PROVIDER_OUT=$("$VENV_PYTHON" -c "
import sys; sys.path.insert(0, '$PROJECT_DIR')
from core import providers
for d in providers.describe_all():
    print(f\"{'OK' if d['available'] else 'NG'}|{d['name']}|{d['reason']}\")
" 2>/dev/null)
    if [ -z "$PROVIDER_OUT" ]; then
        bad "供應商檢查失敗（套件可能沒裝好）" "先解決上面第 1 項"
    else
        AVAIL=0
        while IFS='|' read -r status name reason; do
            if [ "$status" = "OK" ]; then
                ok "$name — $reason"
                AVAIL=$((AVAIL+1))
            else
                echo -e "  ${YELLOW}·${NC} $name — $reason"
            fi
        done <<< "$PROVIDER_OUT"
        if [ "$AVAIL" -eq 0 ]; then
            bad "沒有任何可用的 AI 供應商" "最省事：裝好並登入 Claude Code（終端輸入 claude 能進對話即可），它用你現有的訂閱，不需要 API key"
        fi
    fi
fi

# ── 4. MCP 註冊狀態 ────────────────────────────────
echo -e "\n【4】MCP 入口"
if command -v claude >/dev/null 2>&1; then
    ok "找得到 claude 指令（$(command -v claude)）"
    if claude mcp list 2>/dev/null | grep -q "google-chat"; then
        ok "google-chat 已註冊進 Claude Code"
    else
        warn "google-chat 尚未註冊進 Claude Code" "執行 ./scripts/install-claude.sh"
    fi
else
    warn "PATH 中找不到 claude 指令" "只用 Web 儀表板的話可以忽略；要用 MCP 入口就得先裝 Claude Code"
fi

# ── 5. Web 儀表板 ──────────────────────────────────
echo -e "\n【5】Web 儀表板入口"
if command -v npm >/dev/null 2>&1; then
    ok "找得到 npm（$(npm --version 2>/dev/null)）"
    if [ -f "$PROJECT_DIR/dashboard/frontend/dist/index.html" ]; then
        ok "前端已建置"
    else
        warn "前端尚未建置" "執行 ./scripts/start-web.sh，它會自動建（第一次要幾分鐘）"
    fi
else
    warn "找不到 npm" "只用 MCP 入口的話可以忽略；要用儀表板就得先裝 Node.js"
fi

# ── 總結 ───────────────────────────────────────────
echo -e "\n${CYAN}======================================================${NC}"
if [ "$FAILED" -eq 0 ] && [ "$WARNED" -eq 0 ]; then
    echo -e "${GREEN} 🎉 全部檢查通過，可以開始用了${NC}"
    echo -e "    MCP：在 Claude Code 問「幫我列出 Google Chat 有哪些群組」"
    echo -e "    儀表板：./scripts/start-web.sh"
elif [ "$FAILED" -eq 0 ]; then
    echo -e "${YELLOW} 可以用了，但有 $WARNED 項提醒（見上方 → 的說明）${NC}"
else
    echo -e "${RED} 有 $FAILED 項必須處理${NC}${YELLOW}，另有 $WARNED 項提醒${NC}"
    echo -e " 照上方每個 ${CYAN}→${NC} 的指示做完，再跑一次這個檢查。"
fi
echo -e "${CYAN}======================================================${NC}"

[ "$FAILED" -eq 0 ]
