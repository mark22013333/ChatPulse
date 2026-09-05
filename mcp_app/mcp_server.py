"""ChatPulse MCP Server：把 core/ 的能力暴露成 Claude 可呼叫的四個工具。

規格對應：SPECIFICATION.md 2.1（四個工具全部保留）、5.5（所有入口共用同一組常數）、
8.4（錯誤規格）、十一（D-4 摘要風格）。

三個與 v1 實作的差異：
  1. `limit` 不再各自寫死（原本 30／50 混用），一律取 `core.config.LIMIT_DEFAULT`
     並交給 `core.chat_client.validate_limit()` 驗證
  2. `summarize_chat_space` 新增 `style` 參數（D-4），三種風格輸出章節結構不同
  3. 對話文本組裝改用 `core.chat_client.format_conversation()`，不再各處複製迴圈

MCP 工具的回傳型別是字串，所以這裡把 `ChatPulseError` 轉成看得懂的中文訊息，
不讓 traceback 冒到 Claude 的對話裡。
"""

import os
import sys
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from mcp.server.mcpserver import MCPServer

from core import config as cfg
from core import directory
from core import prompts
from core.chat_client import GoogleChatClient, format_conversation, validate_limit
from core.errors import ChatPulseError, SpaceNotFound
from core import providers

mcp = MCPServer(name="Google Chat Assistant")

# 兩個 client 都改為延遲建立：GoogleChatClient() 會讀憑證（必要時開瀏覽器授權）、
# GeminiClient() 會在缺 GOOGLE_API_KEY 時直接拋錯。放在模組層級會讓「只是 import
# 這個模組」也產生副作用，測試與靜態檢查都跑不動。
_chat_client: Optional[GoogleChatClient] = None
_ai_providers: Dict[str, "providers.AIProvider"] = {}


def get_chat_client() -> GoogleChatClient:
    global _chat_client
    if _chat_client is None:
        _chat_client = GoogleChatClient()
    return _chat_client


def get_ai(name: Optional[str] = None) -> "providers.AIProvider":
    """取得 AI 供應商（延遲建立並快取）。

    name 省略時用 CHATPULSE_AI_PROVIDER（預設別名 `claude`，目前解析為
    本機的 Claude Code CLI）。
    """
    key = (name or "").strip().lower() or "__default__"
    if key not in _ai_providers:
        _ai_providers[key] = providers.resolve(name or None)
    return _ai_providers[key]


def _error_text(exc: ChatPulseError) -> str:
    """把 ChatPulseError 轉成 MCP 工具要回傳的中文字串（8.4）。"""
    return f"❌ [{exc.code}] {exc.message}"


def _resolve_space(space_name_or_id: str) -> Dict[str, Any]:
    """把「群組名稱關鍵字」或「完整 Space ID」解析成 Space 物件。

    找不到時拋 SpaceNotFound，由各工具統一轉成中文訊息。
    """
    if space_name_or_id.startswith("spaces/"):
        return {"name": space_name_or_id, "displayName": space_name_or_id}
    space = get_chat_client().find_space_by_name(space_name_or_id)
    if not space:
        raise SpaceNotFound(f"找不到名稱包含「{space_name_or_id}」的聊天室")
    return space


def _format_last_active(space: Dict[str, Any]) -> str:
    """把 lastActiveTime 轉成好讀的日期時間；沒有這個欄位就標示未知。"""
    raw = space.get("lastActiveTime") or ""
    if not raw:
        return "最後活動：未知"
    return f"最後活動：{raw[:16].replace('T', ' ')}"


@mcp.tool()
def list_chat_spaces(search: str = "") -> str:
    """
    列出使用者加入的 Google Chat 聊天室／空間（自動翻頁，涵蓋全部空間）。
    - search: 選填，依名稱關鍵字過濾（例如 'BU2'、'暫存'、'北市府'）。
    每一列會附上該空間的最後活動時間，方便判斷哪些群組還活著。
    """
    try:
        spaces = get_chat_client().list_spaces()
    except ChatPulseError as exc:
        return _error_text(exc)

    if search:
        keyword = search.lower()
        spaces = [
            s for s in spaces if keyword in (s.get("displayName") or "").lower()
        ]

    if not spaces:
        return f"未找到符合「{search}」的空間。" if search else "未找到任何空間。"

    lines = [f"找到 {len(spaces)} 個空間："]
    for s in spaces:
        name = s.get("displayName") or "（私訊／未命名）"
        stype = s.get("spaceType", "UNKNOWN")
        sid = s.get("name")
        lines.append(
            f"- 【{name}】({stype}) | ID: {sid} | {_format_last_active(s)}"
        )
    return "\n".join(lines)


@mcp.tool()
def fetch_chat_messages(
    space_name_or_id: str, limit: int = cfg.LIMIT_DEFAULT
) -> str:
    """
    抓取指定 Google Chat 空間的最新訊息（由舊到新排列）。
    - space_name_or_id: 群組名稱關鍵字（例如 '0.暫存'）或完整 Space ID（例如 'spaces/AAAAxLxqJxY'）。
    - limit: 抓取則數，允許範圍見系統設定的上下限，超出範圍會回傳參數錯誤。
    """
    try:
        limit = validate_limit(limit)
        space = _resolve_space(space_name_or_id)
        space_id = space["name"]
        display_name = space.get("displayName") or space_id
        messages = get_chat_client().fetch_recent_messages(space_id, limit=limit)
    except ChatPulseError as exc:
        return _error_text(exc)

    if not messages:
        return f"空間「{display_name}」尚無任何訊息。"

    body = format_conversation(messages, directory.safe_resolver(messages))
    header = f"=== 「{display_name}」最近 {len(messages)} 則對話（由舊到新）==="
    return f"{header}\n{body}" if body else f"{header}\n（這些訊息都沒有文字內容）"


@mcp.tool()
def summarize_chat_space(
    space_name_or_id: str,
    limit: int = cfg.LIMIT_DEFAULT,
    style: str = cfg.SUMMARY_STYLE_DEFAULT,
    post_to_chat: bool = False,
    provider: str = "",
) -> str:
    """
    對指定 Google Chat 空間的對話產出結構化摘要（AI 供應商可選，見 provider 參數）。
    - space_name_or_id: 群組名稱關鍵字（例如 '0.暫存'）或 Space ID。
    - limit: 分析的對話則數，超出允許範圍會回傳參數錯誤。
    - style: 摘要風格，三選一（三種風格的輸出章節結構不同）：
        • general      通用：給沒跟到對話的人看，輸出「核心討論主題與脈絡／共識與重要決議／待辦事項」。
        • technical    技術細節：給要接手排查的工程師看，輸出「技術問題與症狀／已排除的假設與排查過程／
                       技術方案與取捨／未解的技術問題／待辦事項」，行政討論壓到最短。
        • action_only  只要待辦：不寫任何脈絡與前言，只輸出待辦事項清單。
    - post_to_chat: 是否把摘要推播回該聊天室（預設 False，只在回應中輸出）。
      設為 True 會以使用者本人身分在該群組發言，請先向使用者確認再帶入。
    - provider: 要用哪個 AI 供應商，留空＝用伺服器預設。可用值：
        • claude      別名，目前解析為本機 Claude Code CLI
        • auto        別名，依序試 claude_cli → gemini，挑第一個可用的
        • claude_cli  本機 Claude Code CLI（吃現有訂閱，不需 API key）
        • gemini      Google Gemini（免費層每天僅 20 次請求）
    """
    try:
        limit = validate_limit(limit)
        style = prompts.validate_style(style)
        space = _resolve_space(space_name_or_id)
        space_id = space["name"]
        display_name = space.get("displayName") or space_id
        messages = get_chat_client().fetch_recent_messages(space_id, limit=limit)
    except ChatPulseError as exc:
        return _error_text(exc)

    if not messages:
        return f"空間「{display_name}」尚無足夠訊息可供摘要。"

    conversation_text = format_conversation(messages, directory.safe_resolver(messages))
    if not conversation_text:
        return f"空間「{display_name}」最近的訊息都沒有文字內容，無法摘要。"

    try:
        ai = get_ai(provider)
        summary = ai.generate(
            prompts.summary_prompt(
                display_name, conversation_text, len(messages), style
            ),
            operation="summarize",
        )
    except ChatPulseError as exc:
        return _error_text(exc)

    if post_to_chat:
        try:
            get_chat_client().send_message(space_id, summary)
            summary += f"\n\n*（已推播至群組「{display_name}」）*"
        except ChatPulseError as exc:
            summary += f"\n\n*（⚠️ 推播失敗：[{exc.code}] {exc.message}）*"

    return summary


@mcp.tool()
def list_ai_providers() -> str:
    """列出可用的 AI 供應商與各自的狀態。

    在 summarize_chat_space 回報配額不足時，可以先用這個查有哪些替代選項，
    再帶 provider 參數重試。
    """
    lines = [f"預設：{providers.default_name()}"]
    for d in providers.describe_all():
        mark = "✅ 可用" if d["available"] else "❌ 不可用"
        lines.append(f"- {d['name']}（{d['label']}）｜模型 {d['model']}｜{mark}")
        lines.append(f"    {d['reason']}")
    return "\n".join(lines)


@mcp.tool()
def send_chat_message(space_name_or_id: str, message_text: str) -> str:
    """
    在指定 Google Chat 空間發送一則文字訊息（以使用者本人身分送出，不是 Bot）。
    - space_name_or_id: 群組名稱關鍵字（例如 '0.暫存'）或 Space ID。
    - message_text: 要傳送的文字內容。
    這個動作會真的在群組留下訊息且無法收回，呼叫前請先向使用者確認目標群組與內容。
    """
    try:
        space = _resolve_space(space_name_or_id)
        space_id = space["name"]
        display_name = space.get("displayName") or space_id
        res = get_chat_client().send_message(space_id, message_text)
    except ChatPulseError as exc:
        return _error_text(exc)

    return f"✅ 已發送訊息至「{display_name}」！\n訊息 ID: {res.get('name')}"


if __name__ == "__main__":
    mcp.run()
