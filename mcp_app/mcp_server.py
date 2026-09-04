import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from mcp.server.mcpserver import MCPServer
from core.chat_client import GoogleChatClient
from core.gemini_client import GeminiClient

mcp = MCPServer(name="Google Chat Assistant")
chat_client = GoogleChatClient()
gemini_client = GeminiClient()

@mcp.tool()
def list_chat_spaces(search: str = "") -> str:
    """
    列出使用者加入的 Google Chat 聊天室/空間。
    可選參數 search: 依名稱關鍵字過濾 (例如 'BU2', '暫存', '北市府')。
    """
    spaces = chat_client.list_spaces()
    if search:
        spaces = [s for s in spaces if search.lower() in (s.get('displayName') or '').lower()]
    
    if not spaces:
        return f"未找到符合 '{search}' 的空間。" if search else "未找到任何空間。"

    lines = [f"找到 {len(spaces)} 個空間:"]
    for s in spaces:
        name = s.get('displayName') or '（私訊/未命名）'
        stype = s.get('spaceType', 'UNKNOWN')
        sid = s.get('name')
        lines.append(f"- 【{name}】 ({stype}) | ID: {sid}")
    return "\n".join(lines)

@mcp.tool()
def fetch_chat_messages(space_name_or_id: str, limit: int = 30) -> str:
    """
    抓取指定 Google Chat 空間的最新訊息。
    - space_name_or_id: 群組名稱關鍵字 (例如 '0.暫存', '1.BU2-PG') 或完整 Space ID (例如 'spaces/AAAAxLxqJxY')。
    - limit: 抓取則數 (預設 30，可指定 50, 100 等)。
    """
    space = None
    if space_name_or_id.startswith("spaces/"):
        space = {"name": space_name_or_id, "displayName": space_name_or_id}
    else:
        space = chat_client.find_space_by_name(space_name_or_id)

    if not space:
        return f"❌ 找不到符合 '{space_name_or_id}' 的空間。"

    space_id = space['name']
    display_name = space.get('displayName', space_id)
    messages = chat_client.fetch_recent_messages(space_id, limit=limit)

    if not messages:
        return f"空間「{display_name}」尚無任何訊息。"

    lines = [f"=== 「{display_name}」最近 {len(messages)} 則對話 (由舊到新) ==="]
    for m in messages:
        sender = m.get('sender', {}).get('displayName', '未知成員')
        create_time = m.get('createTime', '')[:16].replace('T', ' ')
        text = m.get('text', '').strip()
        if text:
            lines.append(f"[{create_time}] {sender}: {text}")

    return "\n".join(lines)

@mcp.tool()
def summarize_chat_space(space_name_or_id: str, limit: int = 50, post_to_chat: bool = False) -> str:
    """
    使用 Gemini 3.6 Flash 對指定 Google Chat 空間的對話進行結構化智慧摘要。
    - space_name_or_id: 群組名稱關鍵字 (例如 '0.暫存', '1.BU2-PG') 或 Space ID。
    - limit: 分析對話則數 (例如 30, 50, 100)。
    - post_to_chat: 是否將摘要結果自動推播回該聊天室 (預設為 False，僅在 AI 回應中輸出)。
    """
    space = None
    if space_name_or_id.startswith("spaces/"):
        space = {"name": space_name_or_id, "displayName": space_name_or_id}
    else:
        space = chat_client.find_space_by_name(space_name_or_id)

    if not space:
        return f"❌ 找不到符合 '{space_name_or_id}' 的空間。"

    space_id = space['name']
    display_name = space.get('displayName', space_id)
    messages = chat_client.fetch_recent_messages(space_id, limit=limit)

    if not messages:
        return f"空間「{display_name}」尚無足夠訊息可供摘要。"

    formatted_lines = []
    for m in messages:
        sender = m.get('sender', {}).get('displayName', '未知成員')
        create_time = m.get('createTime', '')[:16].replace('T', ' ')
        text = m.get('text', '').strip()
        if text:
            formatted_lines.append(f"[{create_time}] {sender}: {text}")

    conversation_text = "\n".join(formatted_lines)
    summary = gemini_client.summarize_discussion(display_name, conversation_text, len(messages))

    if post_to_chat:
        try:
            chat_client.send_message(space_id, summary)
            summary += f"\n\n*(已自動推播至群組「{display_name}」)*"
        except Exception as e:
            summary += f"\n\n*(⚠️ 自動推播失敗: {e})*"

    return summary

@mcp.tool()
def send_chat_message(space_name_or_id: str, message_text: str) -> str:
    """
    在指定 Google Chat 空間發送一則文字訊息。
    - space_name_or_id: 群組名稱關鍵字 (例如 '0.暫存', '1.BU2-PG') 或 Space ID。
    - message_text: 要傳送的文字內容。
    """
    space = None
    if space_name_or_id.startswith("spaces/"):
        space = {"name": space_name_or_id, "displayName": space_name_or_id}
    else:
        space = chat_client.find_space_by_name(space_name_or_id)

    if not space:
        return f"❌ 找不到符合 '{space_name_or_id}' 的空間。"

    space_id = space['name']
    display_name = space.get('displayName', space_id)
    res = chat_client.send_message(space_id, message_text)
    return f"✅ 已成功發送訊息至「{display_name}」！\n訊息 ID: {res.get('name')}"

if __name__ == "__main__":
    mcp.run()
