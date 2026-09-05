"""ChatPulse 設定：純讀環境變數，不含任何硬編碼憑證。

規格對應：SPECIFICATION.md 3.3（憑證絕不進 repo）、5.5（參數統一）、九（資料模型）。
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.environ.get("CHATPULSE_DATA_DIR", os.path.join(BASE_DIR, "data"))

# --- Google Chat OAuth ---
CLIENT_SECRET_FILE = os.environ.get(
    "CHATPULSE_CLIENT_SECRET", os.path.join(CONFIG_DIR, "client_secret.json")
)
# Phase 1 之前的單人 token。Phase 2 起憑證存於 credentials 表，此檔僅供 bootstrap 匯入。
LEGACY_TOKEN_FILE = os.path.join(CONFIG_DIR, "google_chat_token.json")
TOKEN_FILE = LEGACY_TOKEN_FILE  # 相容既有 mcp_app / summarizer 呼叫

# 讀寫 Google Chat 所需（SPECIFICATION.md 4.2）
CHAT_SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.messages.create",
]

# Phase 2 另需身分 scope，用於取得 Viewer 自己的 Google user id（4.2、8.2）
IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# 儀表板登入使用的完整 scope 組合
DASHBOARD_SCOPES = CHAT_SCOPES + IDENTITY_SCOPES

# --- AI 供應商選擇 ---
# "claude" 是智慧別名：有 ANTHROPIC_API_KEY 就走 API，否則走本機的 Claude Code CLI。
# 也可以指定 "claude_api"／"claude_cli"／"gemini" 強制走特定一條。
AI_PROVIDER = os.environ.get("CHATPULSE_AI_PROVIDER", "claude")
AI_PROVIDERS = ("gemini", "claude_api", "claude_cli")

# --- Claude（Anthropic API）---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_API_MODEL = os.environ.get("CHATPULSE_CLAUDE_API_MODEL", "claude-opus-5")
# effort 控制思考深度與 token 花費（low／medium／high／xhigh／max）。
# 留空＝用 API 預設（high）。摘要這種工作用 medium 通常就夠，但降不降是
# 使用者的成本決定，不預設幫他降。
CLAUDE_API_EFFORT = os.environ.get("CHATPULSE_CLAUDE_API_EFFORT", "")

# --- Claude Code CLI ---
CLAUDE_CLI_BIN = os.environ.get("CHATPULSE_CLAUDE_BIN", "claude")
# CLI 的 --model 吃別名（opus／sonnet／fable）或完整模型名
CLAUDE_CLI_MODEL = os.environ.get("CHATPULSE_CLAUDE_CLI_MODEL", "opus")
CLAUDE_CLI_TIMEOUT = int(os.environ.get("CHATPULSE_CLAUDE_CLI_TIMEOUT", "600"))
# CLI 預設會載入 Claude Code 自己的 system prompt、CLAUDE.md 與全部工具定義，
# 實測一個 2-token 的 prompt 也會寫入 19,085 token 的快取（約 $0.077）。
# 停掉工具與 MCP、並用自己的 system prompt 取代之後，同一個請求降到 0 token
# 快取寫入、約 $0.0006。摘要用不到任何工具，所以一律關掉。
CLAUDE_CLI_DISABLED_TOOLS = (
    "Task,Bash,Edit,Write,Read,Glob,Grep,NotebookEdit,WebFetch,WebSearch,TodoWrite,"
    "SendMessage,ListAgents,Artifact,Skill,ToolSearch,CronCreate,CronDelete,CronList,"
    "DesignSync,EnterWorktree,ExitWorktree,ReportFindings,ScheduleWakeup,Workflow,"
    "EnterPlanMode,ExitPlanMode,SendUserFile,TaskOutput,TaskStop,BashOutput,KillShell"
)

# --- Gemini API ---
GEMINI_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.environ.get("CHATPULSE_GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# 缺陷 D-3：2048 會讓 500 則對話的結構化摘要被截斷
GEMINI_MAX_OUTPUT_TOKENS = 16384
GEMINI_TEMPERATURE = 0.3

# --- 訊息抓取則數（SPECIFICATION.md 5.5，四個入口共用同一組常數）---
LIMIT_DEFAULT = 50
LIMIT_MIN = 1
LIMIT_MAX = 1000  # 對齊官方 spaces.messages.list 的 pageSize 上限

# 單次 API 呼叫的分頁上限
MESSAGES_PAGE_SIZE = 1000
SPACES_PAGE_SIZE = 1000
MAX_PAGES = 50  # 安全閥：避免 nextPageToken 異常時無限迴圈

# --- 摘要風格（缺陷 D-4）---
SUMMARY_STYLES = ("general", "technical", "action_only")
SUMMARY_STYLE_DEFAULT = "general"

# --- 儲存 ---
DB_PATH = os.environ.get("CHATPULSE_DB", os.path.join(DATA_DIR, "chatpulse.db"))
# 加密金鑰刻意不與資料庫同檔存放（SPECIFICATION.md 九節設計要點）
ENCRYPTION_KEY_FILE = os.environ.get(
    "CHATPULSE_KEY_FILE", os.path.join(DATA_DIR, "token.key")
)

# 保留策略（九節）
SUMMARY_RETENTION_DAYS = 90
MENTION_RETENTION_DAYS = 90

# --- Mention 採集器（SPECIFICATION.md 六節）---
# 實作 A（跨群搜尋）於 2026-09-05 實測不可用：回 200 但恆 0 筆，正對照亦搜不到。
# 詳見 docs/R1-findings.md。預設走實作 B（逐群輪詢 + lastActiveTime 預篩）。
MENTION_COLLECTOR = os.environ.get("CHATPULSE_COLLECTOR", "polling")  # polling | search
MENTION_POLL_INTERVAL_SECONDS = int(os.environ.get("CHATPULSE_POLL_INTERVAL", "45"))
# 16 併發掃 30 個 Space 實測 1.26 秒；外插推算全量 436 個約 18 秒（未實跑全量）
MENTION_POLL_WORKERS = 16
# 首次採集（無上次輪詢時間）往回看多久
MENTION_INITIAL_LOOKBACK_HOURS = 24

# --- Google Chat 限流重試（SPECIFICATION.md 8.4）---
CHAT_RETRY_MAX_ATTEMPTS = 3
CHAT_RETRY_BASE_DELAY = 1.0

# --- Session ---
SESSION_COOKIE_NAME = "chatpulse_session"
SESSION_TTL_HOURS = 24 * 14

# --- 開發／測試用逃生門 ---
# 舊的三 scope token 沒有 userinfo 權限，無法解析身分。匯入時可用此變數補上
# 已知的 Google user id（取得方式見 docs/R1-findings.md）。
BOOTSTRAP_USER_ID = os.environ.get("CHATPULSE_BOOTSTRAP_USER_ID", "")
BOOTSTRAP_EMAIL = os.environ.get("CHATPULSE_BOOTSTRAP_EMAIL", "")


def ensure_data_dir() -> str:
    """建立資料目錄（含 0700 權限），回傳路徑。"""
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    return DATA_DIR
