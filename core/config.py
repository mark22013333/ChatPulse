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
# "claude" 是別名，目前解析到本機的 Claude Code CLI；"auto" 會依序試
# claude_cli → gemini。也可以直接指定 "claude_cli"／"gemini" 強制走特定一條。
# 合法名稱的單一事實來源是 core.providers.VALID_NAMES，這裡不另列一份。
AI_PROVIDER = os.environ.get("CHATPULSE_AI_PROVIDER", "claude")

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

# --- Draft Reply 的脈絡窗（docs/draft-context-design.md）---
# **刻意不與 LIMIT_* 共用。** 摘要的 N 是「要摘多少東西」（產出涵蓋範圍），
# 草稿的 K 是「要理解到多深」（輸入理解深度）。共用的話，使用者把摘要從 50
# 調到 20（因為摘要讀起來太長）會靜默劣化草稿品質，而且 UI 上沒有任何跡象。
DRAFT_WINDOW_FETCH = 60  # 為了找到錨點一次撈多少則
DRAFT_CTX_BEFORE = 15  # 錨點之前保留幾則
DRAFT_CTX_AFTER = 10  # 錨點之後保留幾則（用途是偵測「已經有人回答了」）
# 時間上界過濾後，錨點前至少保留幾則。這個保底是關鍵：
#   只用則數窗 → 冷清的私訊會撈到三個月前的閒聊當「脈絡」
#   只用時間窗 → 冷清的私訊會一則都不剩，等於沒修
#   交集 ＋ 保底 → 熱絡對話被 48h 截斷（正確）、冷清對話至少拿到最近 6 則（正確）
DRAFT_CTX_MIN_BEFORE = 6
DRAFT_WINDOW_HOURS = 48
# 取代 draft 路徑原本寫死的 LIMIT_MAX（1000）。一個很長的討論串會把 1000 則
# 灌進 prompt，而 Reference Space 那邊有 limit 卡著——系統在兩端都失控，只是方向相反。
DRAFT_THREAD_LIMIT = 60
# 群組薄串（有人 @ 你但還沒人回）才會用到的跨串小窗
DRAFT_CROSS_FETCH = 25
DRAFT_CROSS_BEFORE = 8
# 跨串小窗的總開關。它的警語有沒有用是 prompt 工程的假設，沒有實測支撐；
# 若實測發現群組草稿開始張冠李戴，關掉這個比繼續加強語氣正確。
DRAFT_CROSS_THREAD_ENABLED = os.environ.get(
    "CHATPULSE_DRAFT_CROSS_THREAD", "1"
).strip().lower() not in ("0", "false", "no")
# 貼圖脈絡的有效距離比文字短得多，所以圖片只往回看這麼多則
DRAFT_IMAGE_BEFORE = 6
# 同一人連續發話視為「同一個問題」的間隔上限。私訊常見形態是把一個問題拆三則發
# （「你好」「想問一下 X」「方便的話今天回我」）。
#
# **注意這個數字現在只用來「分群」，不用來決定要回哪幾則。** 一開始它兩件事都管，
# 結果 2026-09-07 踩到：對方 11:33 問白名單、12:32 問 LINE 推播，相隔 59 分鐘，
# 於是只有後面那則被當成「要回的」，前面那則掉進背景脈絡，草稿就只回了一半
# （另一半被寫成「我另外看，確認完再回你」）。
# 真正的判準是「從我上次發言到現在，對方講了什麼我還沒回」，與間隔多久無關。
DRAFT_ANCHOR_RUN_GAP_MINUTES = 5
# 一次最多把幾個「未回覆的問題」標成要回的（同一群連發算一個）。超出的較舊問題
# 仍然看得到，只是留在脈絡裡不標。設 3 是品質限制不是技術限制——一則回話同時
# 回四五個不相干的問題就不像人話了，那種情況該分開回。
DRAFT_ANCHOR_MAX_CLUSTERS = 3

# --- 訊息圖片 ---
# 總開關。關掉之後圖片只會以佔位符出現在對話文本裡（AI 知道有圖但看不到內容），
# 不會有任何圖片位元組離開 Google。
# 注意：規格第十節「所有可讀 Space 都可送 AI」是在**純文字**前提下做的決定。
# 截圖是無定向的畫面捕捉（終端 scrollback、其他客戶名稱、DB 查詢結果都可能一起入鏡），
# 風險輪廓與文字不同。要對特定 Space 收緊時，用 IMAGE_EXCLUDED_SPACE_IDS。
IMAGE_ENABLED = os.environ.get("CHATPULSE_IMAGES", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
# 逗號分隔的 space id；列在這裡的聊天室一律只給佔位符，不送圖片內容
IMAGE_EXCLUDED_SPACE_IDS = tuple(
    s.strip()
    for s in os.environ.get("CHATPULSE_IMAGE_EXCLUDED_SPACES", "").split(",")
    if s.strip()
)

# 送出前一律縮圖。實測成本與像素數成正比（約 750 像素／token，三種尺寸一致），
# 且 API **不會**替你自動縮圖——1920×1080 會照 2,073,600 個像素全額計費（2,694 tokens）。
# 縮到長邊 1024 可省約 61%，這是整個成本控制的地基。
IMAGE_MAX_EDGE = int(os.environ.get("CHATPULSE_IMAGE_MAX_EDGE", "1024"))
IMAGE_PIXELS_PER_TOKEN = 750  # 實測值，用於預算估算

# 預算以 token 計而不是張數：同樣「一張圖」在不同尺寸下差 4.2 倍，用張數控管沒有意義
IMAGE_BUDGET_TOKENS_SUMMARY = int(os.environ.get("CHATPULSE_IMAGE_BUDGET_SUMMARY", "6000"))
IMAGE_BUDGET_TOKENS_DRAFT = int(os.environ.get("CHATPULSE_IMAGE_BUDGET_DRAFT", "8000"))
IMAGE_MAX_COUNT = int(os.environ.get("CHATPULSE_IMAGE_MAX_COUNT", "8"))
# 摘要只看最近這麼多則裡的圖（整份 500 則的圖片全抓沒有意義也付不起）
IMAGE_SCAN_RECENT_MESSAGES = 30
# 單一原始檔超過這個大小就跳過，不下載（避免一張 20MB 的圖拖垮整輪）
IMAGE_MAX_SOURCE_BYTES = 12 * 1024 * 1024
IMAGE_DOWNLOAD_WORKERS = 4  # 附件下載另有「每個 Space 每秒 15 次」的限制

# --- 參考專案原始碼（ADR-0006）---
# 總開關。關掉之後 Draft Reply 完全不碰 git，行為與加這個功能之前一致。
# 注意：這功能會把**公司專有原始碼**送進 AI 供應商。規格第十節當初承擔的是
# 「所有可讀 Space 的對話」，不是原始碼——風險輪廓不同，見該節。
CODE_ENABLED = os.environ.get("CHATPULSE_CODE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

# 環境是封閉字彙，不接受自由文字。理由：一旦允許自由填，實務上必然出現
# uat／UAT／staging 三種寫法指同一個分支，而模型引用時會照著講，
# 於是「這是正式環境的行為」這句話就不可信了——那正是這個功能要解決的問題。
CODE_ENVIRONMENTS = ("production", "uat", "dev")
CODE_ENV_DEFAULT = "production"
#: 顯示用中文名。prompt 與前端都用這份，避免兩邊各翻一次而不一致。
CODE_ENV_LABELS = {
    "production": "正式環境",
    "uat": "UAT 環境",
    "dev": "開發環境",
}

# 預算比圖片再小一些：草稿是聊天回話，不是 code review。
# 12k tokens 大約是 8~12 段函式，足夠回答「這段邏輯為什麼這樣寫」。
CODE_BUDGET_TOKENS_DRAFT = int(os.environ.get("CHATPULSE_CODE_BUDGET", "12000"))
CODE_MAX_HITS_PER_PROJECT = 12
# 同一份草稿最多查兩個專案。設 2 而不是 1，是為了讓「正式 vs UAT 比對」
# 這個最有價值的用法成立（同一個專案送兩次、環境不同）。
CODE_MAX_PROJECTS_PER_DRAFT = 2
CODE_CONTEXT_LINES = 12  # 命中行前後各取幾行
CODE_MAX_TERMS = 8
CODE_MAX_FILE_BYTES = 512 * 1024  # 超過通常是產生檔或壓縮資料，讀了也沒用

CODE_GIT_BIN = os.environ.get("CHATPULSE_GIT_BIN", "git")
CODE_GIT_TIMEOUT = int(os.environ.get("CHATPULSE_GIT_TIMEOUT", "20"))
# 分支太久沒有新 commit 時提醒可能忘了 fetch。無法區分「穩定的 release 分支」
# 與「忘記 fetch」，所以只提醒不擋。
CODE_STALE_BRANCH_DAYS = int(os.environ.get("CHATPULSE_CODE_STALE_DAYS", "30"))

#: git pathspec 的排除語法。這些目錄搜到了也只會浪費預算。
CODE_DEFAULT_EXCLUDE_GLOBS = (
    ":(exclude)**/node_modules/**",
    ":(exclude)**/dist/**",
    ":(exclude)**/build/**",
    ":(exclude)**/vendor/**",
    ":(exclude)**/__pycache__/**",
    ":(exclude)**/.venv/**",
    ":(exclude)**/*.min.js",
    ":(exclude)**/*.map",
    ":(exclude)**/*.lock",
    ":(exclude)**/*.snap",
)

#: 這些路徑整份跳過，不進 prompt。機敏遮蔽是 best-effort，
#: 但「整個檔案就是憑證」的情況可以直接用路徑擋掉，成本低、效果確定。
CODE_SECRET_PATH_PATTERNS = (
    "**/.env",
    "**/.env.*",
    "**/*secret*",
    "**/*credential*",
    "**/id_rsa*",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
)

# --- ZPlanner 工時系統（Draft Worklog 的基礎層）---
# **網址與 token 都純讀環境變數，兩者都沒有 fallback 預設值。**
#
# 這是缺陷 D-2 的處置直接套用過來（SPECIFICATION.md 3.3、787 行）：那次是
# Gemini 金鑰被硬編碼成 fallback，理由寫得很清楚——「repo 要交給團隊使用，
# 等於把金鑰一併發出」。ZPlanner 的網址不是憑證，但它是**公司內部系統位置**，
# 同樣沒有理由跟著 repo 一起發出去；而且 GEMINI_API_BASE 那種公開服務的
# endpoint 可以寫死，內部系統不行，兩者不是同一類東西。
#
# 缺少時不在這裡報錯（本檔一貫不做驗證），而是由 ZPlannerClient.available()
# 回一句可讀的中文、_request() 拋 CONFIGURATION_ERROR，與 GEMINI_API_KEY 同型。
ZPLANNER_BASE_URL = os.environ.get("ZPLANNER_BASE_URL", "")

# **刻意不加 CHATPULSE_ 前綴**，與本檔其他每一個變數都不同。理由：這把 token
# 由 ZPlanner 的 /api/tokens/ 發出，使用者的 shell 設定檔裡早就有 ZPLANNER_APIKEY
# 這個名字了。改成 CHATPULSE_ZPLANNER_APIKEY 只會逼每個人多設一份同值的環境變數，
# 然後在兩份之中挑一份忘記更新。前綴的用途是避免撞名，而這個名字本來就不會撞。
ZPLANNER_APIKEY = os.environ.get("ZPLANNER_APIKEY", "")

# (連線, 讀取)。ZPlanner 在內網，連得上就會很快；讀取給得比 persona_sources
# 的 15 秒寬一點，是因為專案 issue 列舉在大專案上實測會慢。
ZPLANNER_TIMEOUT = (5, 20)

# 列舉時每頁抓幾筆。**參數名是 per_page，不是 page_size**——後者是無效參數，
# 而且 ZPlanner 收到會**靜默忽略**、照樣回預設的 20 筆，不報任何錯。
# 2026-09-11 實測：上限 200，超過會被夾到 200（同樣不報錯）。
ZPLANNER_PAGE_SIZE = 200
ZPLANNER_PAGE_SIZE_MAX = 200
# 安全閥，用途同上面的 MAX_PAGES：分頁欄位異常時避免無限迴圈
ZPLANNER_MAX_PAGES = 50

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
