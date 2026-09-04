"""缺陷 D-3 驗證：500 則對話的摘要不被截斷。

規格 SPECIFICATION.md 十一節 D-3：`maxOutputTokens: 2048` 會讓 500 則對話的
結構化摘要被截斷，調整為 16384。

**做法是正對照**：拿同一份 500 則對話，用 2048 與 16384 各跑一次，證明
  (a) 2048 真的會被截斷（finishReason=MAX_TOKENS）
  (b) 16384 不會（finishReason=STOP，且四個章節齊全）
只驗「16384 沒被截斷」是不夠的——那無法排除「這份對話本來就短，2048 也夠」，
而那樣就等於什麼都沒證明。
"""

import json
import os
import sys

sys.path.insert(0, "/Users/cheng/google-chat-bot")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

from core import config as cfg  # noqa: E402
from core import directory, prompts  # noqa: E402
from core import db  # noqa: E402
from core.chat_client import GoogleChatClient, format_conversation  # noqa: E402
from e2e_lib import blocked, check, info, section, set_report, summary  # noqa: E402

REPORT = os.environ.get(
    "E2E_REPORT",
    "/private/tmp/claude-501/-Users-cheng-google-chat-bot/e23e39c0-7bfd-493e-8211-f63e32bb9432/scratchpad/e2e-d3.md",
)
TARGET_MESSAGES = 500


def call_gemini(prompt: str, max_output_tokens: int) -> dict:
    """非串流呼叫，因為要讀 finishReason——串流的最後一塊不一定帶得到。"""
    # 金鑰走標頭，不放 query string——放 URL 的話 raise_for_status() 的
    # 錯誤訊息會把整把金鑰印出來（本測試最初就踩過這一次）
    url = f"{cfg.GEMINI_API_BASE}/models/{cfg.GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": cfg.GEMINI_TEMPERATURE,
            "maxOutputTokens": max_output_tokens,
        },
    }
    resp = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": cfg.GEMINI_API_KEY},
        timeout=600,
    )
    if resp.status_code == 429:
        # 配額用盡不是產品缺陷，交給呼叫端標成「無法驗證」
        return {"quota_exceeded": True, "text": "", "finish_reason": None, "usage": {}}
    resp.raise_for_status()
    data = resp.json()
    cand = (data.get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts") or []
    return {
        "text": "".join(p.get("text", "") for p in parts),
        "finish_reason": cand.get("finishReason"),
        "usage": data.get("usageMetadata", {}),
    }


def main() -> int:
    set_report(REPORT)
    db.init_db()

    section("0. 找一個訊息量足夠的 Space，湊出 500 則對話")
    client = GoogleChatClient(interactive=False)
    spaces = client.list_spaces()
    # 挑成員多、近期有活動的群組，訊息量比較可能夠
    candidates = sorted(
        spaces,
        key=lambda s: -((s.get("membershipCount") or {}).get("joinedDirectHumanUserCount") or 0),
    )[:12]

    chosen = None
    messages = []
    for sp in candidates:
        msgs = client.fetch_recent_messages(sp["name"], limit=TARGET_MESSAGES)
        with_text = [m for m in msgs if (m.get("text") or "").strip()]
        info(f"{(sp.get('displayName') or sp['name'])[:30]}：{len(with_text)} 則有文字")
        if len(with_text) > len(messages):
            chosen, messages = sp, with_text
        if len(messages) >= TARGET_MESSAGES:
            break

    check(
        f"取得足夠的對話（目標 {TARGET_MESSAGES} 則）",
        len(messages) >= 200,
        f"實際 {len(messages)} 則，來自「{chosen.get('displayName')}」",
    )
    if len(messages) < 200:
        return summary()

    directory.learn_from_messages(messages)
    conversation = format_conversation(messages, directory.make_resolver())
    prompt = prompts.summary_prompt(
        chosen.get("displayName") or chosen["name"],
        conversation,
        len(messages),
        "general",
    )
    info(f"對話文本 {len(conversation)} 字，prompt {len(prompt)} 字")

    section("1. 舊值 maxOutputTokens=2048（負對照：應該被截斷）")
    old = call_gemini(prompt, 2048)
    if old.get("quota_exceeded"):
        blocked("2048 會被截斷（finishReason=MAX_TOKENS）", "Gemini 配額用盡")
        blocked("2048 的輸出章節不齊（證明截斷造成資訊遺失）", "Gemini 配額用盡")
        blocked("16384 不會被截斷（finishReason=STOP）", "Gemini 配額用盡")
        blocked("16384 的三個章節齊全", "Gemini 配額用盡")
        check(
            "設定值為 16384（靜態可驗，不需 API）",
            cfg.GEMINI_MAX_OUTPUT_TOKENS == 16384,
            str(cfg.GEMINI_MAX_OUTPUT_TOKENS),
        )
        info("D-3 的正負對照各需一次 Gemini 呼叫，配額恢復後單獨重跑本套件即可")
        return summary()
    info(f"finishReason={old['finish_reason']}，輸出 {len(old['text'])} 字，usage={json.dumps(old['usage'])}")
    check(
        "2048 會被截斷（finishReason=MAX_TOKENS）",
        old["finish_reason"] == "MAX_TOKENS",
        f"finishReason={old['finish_reason']}",
    )
    old_sections = [s for s in ("核心討論主題", "共識與重要決議", "待辦事項") if s in old["text"]]
    check(
        "2048 的輸出章節不齊（證明截斷造成資訊遺失）",
        len(old_sections) < 3,
        f"只有 {old_sections}",
    )

    section("2. 新值 maxOutputTokens=16384（正式設定：不應被截斷）")
    new = call_gemini(prompt, cfg.GEMINI_MAX_OUTPUT_TOKENS)
    if new.get("quota_exceeded"):
        blocked("16384 不會被截斷（finishReason=STOP）", "Gemini 配額用盡")
        blocked("16384 的三個章節齊全", "Gemini 配額用盡")
        return summary()
    info(f"finishReason={new['finish_reason']}，輸出 {len(new['text'])} 字，usage={json.dumps(new['usage'])}")
    check(
        f"設定值為 16384",
        cfg.GEMINI_MAX_OUTPUT_TOKENS == 16384,
        str(cfg.GEMINI_MAX_OUTPUT_TOKENS),
    )
    check(
        "16384 不會被截斷（finishReason=STOP）",
        new["finish_reason"] == "STOP",
        f"finishReason={new['finish_reason']}",
    )
    new_sections = [s for s in ("核心討論主題", "共識與重要決議", "待辦事項") if s in new["text"]]
    check(
        "16384 的三個章節齊全",
        len(new_sections) == 3,
        f"{new_sections}",
    )
    check(
        "新值輸出明顯長於舊值",
        len(new["text"]) > len(old["text"]),
        f"{len(old['text'])} 字 -> {len(new['text'])} 字",
    )
    # maxOutputTokens 是「思考 + 正文」的總預算，不是只算正文。
    # 這一點決定了 D-3 的真正根因：2048 那次 thoughtsTokenCount 就吃掉 1965，
    # 只剩 79 token 寫正文，所以輸出幾乎是空的——不是「摘要太長」，
    # 而是「思考預算把正文擠掉了」。
    old_budget = (old["usage"].get("thoughtsTokenCount") or 0) + (
        old["usage"].get("candidatesTokenCount") or 0
    )
    new_budget = (new["usage"].get("thoughtsTokenCount") or 0) + (
        new["usage"].get("candidatesTokenCount") or 0
    )
    check(
        "2048 那次的預算被思考 token 吃光（正文只剩零星幾十 token）",
        (old["usage"].get("thoughtsTokenCount") or 0) > 1000
        and (old["usage"].get("candidatesTokenCount") or 0) < 200,
        f"thoughts={old['usage'].get('thoughtsTokenCount')} "
        f"candidates={old['usage'].get('candidatesTokenCount')} 合計={old_budget}",
    )
    check(
        "完整輸出所需的總預算（思考+正文）超過舊上限 2048",
        new_budget > 2048,
        f"thoughts={new['usage'].get('thoughtsTokenCount')} "
        f"candidates={new['usage'].get('candidatesTokenCount')} 合計={new_budget} > 2048",
    )
    info(
        "結論：maxOutputTokens 含 thinking token，這是 2048 會截斷的實際原因，"
        "規格書 D-3 的描述（「500 則對話的摘要會被截斷」）方向對但機制不同"
    )

    return summary()


if __name__ == "__main__":
    sys.exit(main())
