"""CLI 版單群摘要器。

規格對應：SPECIFICATION.md 2.4（既有 CLI 資產）、5.4（任何送出都要明確確認）、
5.5（所有入口共用同一組常數）、8.4（錯誤規格）、十一（D-4 摘要風格）。

三個與 v1 實作的差異：
  1. `--count` 預設改吃 `core.config.LIMIT_DEFAULT`，並交給 `validate_limit()` 驗證，
     超出範圍時印中文錯誤並以非 0 退出，不噴 traceback
  2. 新增 `--style`（general／technical／action_only），選項直接取自 core 的常數
  3. **推播預設關閉**：原本不加參數就會推播回群組，現在必須明確加 `--post`，
     且會先印出目標 Space 並要求二次確認（5.4）
"""

import os
import sys

# 加入根目錄至 sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import config as cfg
from core import directory
from core import prompts
from core.chat_client import GoogleChatClient, format_conversation, validate_limit
from core.errors import ChatPulseError
from core.gemini_client import GeminiClient


def confirm_post(space_name: str, space_id: str) -> bool:
    """推播前的二次確認（5.4）。非互動環境一律視為不同意，避免無人值守時誤送。"""
    print("\n" + "!" * 50)
    print("⚠️  即將把摘要以「你本人的身分」發送到下列聊天室：")
    print(f"    群組名稱：{space_name}")
    print(f"    Space ID：{space_id}")
    print("    訊息送出後無法收回。")
    print("!" * 50)

    if not sys.stdin.isatty():
        print("❌ 目前不是互動式終端，無法完成二次確認，已取消推播。")
        return False

    answer = input("確定要推播嗎？輸入 y 確認，其他任何輸入皆取消：").strip().lower()
    if answer == "y":
        return True
    print("已取消推播。")
    return False


def run_summary(
    space_query: str = "0.暫存",
    count: int = cfg.LIMIT_DEFAULT,
    style: str = cfg.SUMMARY_STYLE_DEFAULT,
    post_back: bool = False,
):
    """執行摘要流程。

    1. 驗證參數（則數上下限、摘要風格）
    2. 找到指定群組
    3. 抓取指定則數的訊息
    4. 用 core 的 format_conversation() 組合對話文本
    5. 呼叫 Gemini 產出摘要
    6. （只有明確要求時）二次確認後推播回 Google Chat 群組
    """
    count = validate_limit(count)
    style = prompts.validate_style(style)

    print("🚀 初始化 Google Chat 與 Gemini 模組...")
    chat = GoogleChatClient()
    gemini = GeminiClient()

    print(f"🔍 正在尋找空間：「{space_query}」...")
    space = chat.find_space_by_name(space_query)
    if not space:
        print(f"❌ 找不到名稱包含「{space_query}」的聊天室空間！")
        return None

    space_id = space["name"]
    space_name = space.get("displayName") or space_id
    print(f"✅ 目標空間：{space_name}（{space_id}）")

    print(f"📥 正在抓取最近 {count} 則訊息（自動處理分頁）...")
    messages = chat.fetch_recent_messages(space_id, limit=count)
    print(f"✅ 成功抓取 {len(messages)} 則訊息！")

    if not messages:
        print("⚠️ 空間內無訊息可摘要。")
        return None

    conversation_text = format_conversation(messages, directory.safe_resolver(messages))
    if not conversation_text:
        print("⚠️ 這些訊息都沒有文字內容，無法摘要。")
        return None

    print(f"🧠 正在將對話送交 Gemini（風格：{style}）進行深度分析...")
    summary = gemini.summarize_discussion(
        space_name, conversation_text, len(messages), style
    )

    print("\n" + "=" * 50)
    print("📋 【Gemini 產出摘要結果】：")
    print("=" * 50)
    print(summary)
    print("=" * 50 + "\n")

    if post_back:
        if confirm_post(space_name, space_id):
            print(f"📤 正在將摘要推播回群組「{space_name}」...")
            chat.send_message(space_id, summary)
            print("🎉 推播完成！")
    else:
        print("ℹ️ 未推播回群組（要推播請加上 --post）。")

    return summary


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Google Chat 對話智慧摘要器（CLI 入口）"
    )
    parser.add_argument(
        "--space", "-s", default="0.暫存", help="目標群組名稱關鍵字"
    )
    parser.add_argument(
        "--count",
        "-c",
        # 刻意收成字串再自己驗：argparse 的 type=int 會用它內建的英文訊息
        # 直接 exit(2)，而規格 5.5 要求四個入口對非法 limit 的行為一致
        # （同一組中文訊息、同一個 INVALID_PARAMETER 代碼）。
        default=str(cfg.LIMIT_DEFAULT),
        help=(
            f"抓取訊息則數，可用範圍 {cfg.LIMIT_MIN}~{cfg.LIMIT_MAX}"
            f"（預設 {cfg.LIMIT_DEFAULT}）"
        ),
    )
    parser.add_argument(
        "--style",
        choices=list(cfg.SUMMARY_STYLES),
        default=cfg.SUMMARY_STYLE_DEFAULT,
        help=(
            "摘要風格：general＝通用（脈絡＋決議＋待辦）；"
            "technical＝技術細節（症狀／已排除假設／方案取捨／未解問題＋待辦）；"
            "action_only＝只輸出待辦事項"
            f"（預設 {cfg.SUMMARY_STYLE_DEFAULT}）"
        ),
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help="摘要完成後推播回群組（會先顯示目標群組並要求二次確認）",
    )
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="明確指定不推播（預設行為；與 --post 併用時以本旗標為準）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    # 預設不推播；--no-post 是保守側，與 --post 併用時勝出
    post_back = args.post and not args.no_post

    try:
        # validate_limit 同時處理「非整數」與「超出範圍」，回同一個 INVALID_PARAMETER
        count = validate_limit(args.count)
        run_summary(
            space_query=args.space,
            count=count,
            style=args.style,
            post_back=post_back,
        )
    except ChatPulseError as exc:
        # 8.4：可預期的錯誤只印中文訊息，不讓 traceback 冒到終端
        print(f"❌ [{exc.code}] {exc.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中斷。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
