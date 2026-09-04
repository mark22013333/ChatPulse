import sys
import os

# 加入根目錄至 sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.chat_client import GoogleChatClient
from core.gemini_client import GeminiClient

def run_summary(space_query="0.暫存", count=30, post_back=True):
    """
    執行摘要流程：
    1. 找到指定群組
    2. 抓取指定筆數 (如 100 筆)
    3. 格式化對話文本
    4. 呼叫 Gemini 產出摘要
    5. (可選) 推播回 Google Chat 群組
    """
    print(f"🚀 初始化 Google Chat 與 Gemini 模組...")
    chat = GoogleChatClient()
    gemini = GeminiClient()

    print(f"🔍 正在尋找空間: '{space_query}'...")
    space = chat.find_space_by_name(space_query)
    if not space:
        print(f"❌ 找不到包含 '{space_query}' 的聊天室空間！")
        return None

    space_id = space['name']
    space_name = space.get('displayName', space_id)
    print(f"✅ 目標空間：{space_name} ({space_id})")

    print(f"📥 正在抓取最近 {count} 則訊息 (自動處理分頁)...")
    messages = chat.fetch_recent_messages(space_id, limit=count)
    print(f"✅ 成功抓取 {len(messages)} 則訊息！")

    if not messages:
        print("⚠️ 空間內無訊息可摘要。")
        return None

    # 組合對話文本
    formatted_lines = []
    for m in messages:
        sender = m.get('sender', {}).get('displayName', '未知成員')
        create_time = m.get('createTime', '')[:16].replace('T', ' ')
        text = m.get('text', '').strip()
        if text:
            formatted_lines.append(f"[{create_time}] {sender}: {text}")

    conversation_text = "\n".join(formatted_lines)

    print(f"🧠 正在將對話送交 Gemini 3.6 Flash 進行深度分析...")
    summary = gemini.summarize_discussion(space_name, conversation_text, len(messages))

    print("\n" + "=" * 50)
    print("📋 【Gemini 產出摘要結果】:")
    print("=" * 50)
    print(summary)
    print("=" * 50 + "\n")

    if post_back:
        print(f"📤 正在將摘要推播回群組「{space_name}」...")
        chat.send_message(space_id, summary)
        print("🎉 推播完成！")

    return summary

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Google Chat 對話智慧摘要器")
    parser.add_argument("--space", "-s", default="0.暫存", help="目標群組名稱關鍵字")
    parser.add_argument("--count", "-c", type=int, default=30, help="抓取訊息則數 (例如 50, 100)")
    parser.add_argument("--no-post", action="store_true", help="僅在終端輸出，不推播回群組")

    args = parser.parse_args()
    run_summary(space_query=args.space, count=args.count, post_back=not args.no_post)
