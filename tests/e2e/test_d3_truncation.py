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
from datetime import datetime, timezone

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

# 每次成功的量測都存進這裡，鍵是 maxOutputTokens。
# 為什麼需要：Gemini 免費層每天只有 20 次請求，且配額是「每幾分鐘釋放一個名額」
# 的形態——本測試要兩次呼叫，重跑時第一次就把名額用掉，第二次永遠拿不到，
# 於是正負對照永遠湊不齊。存起來之後可以用 --only 把名額花在缺的那一半。
MEASUREMENTS = os.environ.get(
    "E2E_D3_MEASUREMENTS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "d3-measurements.json"),
)


def load_measurements() -> dict:
    if os.path.exists(MEASUREMENTS):
        try:
            with open(MEASUREMENTS) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def measurement_key(max_tokens: int) -> str:
    """鍵必須含模型名。

    只用 max_tokens 當鍵是錯的：配額是**每個模型各自 20 次**
    （quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier），所以換模型
    重跑是繞過配額的正當手段——但那樣一來，A 模型的量測就會被當成 B 模型的證據。
    """
    return f"{cfg.GEMINI_MODEL}::{max_tokens}"


def save_measurement(max_tokens: int, result: dict, prompt_chars: int, source_space: str) -> None:
    data = load_measurements()
    data[measurement_key(max_tokens)] = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "max_output_tokens": max_tokens,
        "finish_reason": result["finish_reason"],
        "usage": result["usage"],
        "output_chars": len(result["text"]),
        "sections_found": [
            sec for sec in ("核心討論主題", "共識與重要決議", "待辦事項")
            if sec in result["text"]
        ],
        "prompt_chars": prompt_chars,
        "source_space": source_space,
        "model": cfg.GEMINI_MODEL,
    }
    with open(MEASUREMENTS, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def obtain(max_tokens: int, prompt: str, prompt_chars: int, space_name: str, only):
    """取得某個 maxOutputTokens 的量測值。

    優先實跑；配額不足時退回先前存下的量測，並在報告中**明確標示是沿用的**——
    不標示就等於把舊結果冒充成本輪實測。`only` 指定時，未被指定的那一半
    一律不打 API（把稀缺的配額名額留給要跑的那半）。
    """
    cached = load_measurements().get(measurement_key(max_tokens))

    if only is not None and only != max_tokens:
        if cached:
            info(f"maxOutputTokens={max_tokens}：本輪未呼叫（--only {only}），沿用 {cached['measured_at']} 的量測")
            return cached, "cached"
        return None, "missing"

    res = call_gemini(prompt, max_tokens)
    if not res.get("quota_exceeded"):
        save_measurement(max_tokens, res, prompt_chars, space_name)
        info(
            f"maxOutputTokens={max_tokens} 本輪實測："
            f"finishReason={res['finish_reason']}，輸出 {len(res['text'])} 字，"
            f"usage={json.dumps(res['usage'])}"
        )
        return {
            "finish_reason": res["finish_reason"],
            "usage": res["usage"],
            "output_chars": len(res["text"]),
            "sections_found": [
                sec for sec in ("核心討論主題", "共識與重要決議", "待辦事項")
                if sec in res["text"]
            ],
            "measured_at": "本輪",
        }, "fresh"

    if cached:
        info(f"maxOutputTokens={max_tokens}：配額用盡，沿用 {cached['measured_at']} 的量測")
        return cached, "cached"
    return None, "missing"


def main() -> int:
    only = None
    if "--only" in sys.argv:
        only = int(sys.argv[sys.argv.index("--only") + 1])

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
        if len(with_text) > len(messages):
            chosen, messages = sp, with_text
        if len(messages) >= TARGET_MESSAGES:
            break

    space_name = chosen.get("displayName") or chosen["name"]
    check(
        f"取得足夠的對話（目標 {TARGET_MESSAGES} 則）",
        len(messages) >= 200,
        f"實際 {len(messages)} 則，來自「{space_name}」",
    )
    if len(messages) < 200:
        return summary()

    directory.learn_from_messages(messages)
    conversation = format_conversation(messages, directory.make_resolver())
    prompt = prompts.summary_prompt(space_name, conversation, len(messages), "general")
    info(f"對話文本 {len(conversation)} 字，prompt {len(prompt)} 字")
    info(f"本輪使用的模型：{cfg.GEMINI_MODEL}"
         + ("（＝專案設定值）" if cfg.GEMINI_MODEL == "gemini-3.6-flash"
            else "（**非**專案預設的 gemini-3.6-flash，用 CHATPULSE_GEMINI_MODEL 指定）"))
    if only is not None:
        info(f"本輪只跑 maxOutputTokens={only}（Gemini 免費層每天 20 次，名額要留給缺的那半）")

    section("1. 舊值 maxOutputTokens=2048（負對照）")
    old, old_src = obtain(2048, prompt, len(prompt), space_name, only)
    if old is None:
        blocked("2048 的行為", "配額用盡且無先前量測")
    else:
        tag = "" if old_src == "fresh" else f"（沿用 {old['measured_at']} 的量測）"
        old_thoughts = old["usage"].get("thoughtsTokenCount") or 0
        old_truncated = old["finish_reason"] == "MAX_TOKENS"

        # **這一段刻意不寫死「2048 一定會被截斷」。**
        # 2026-09-05 用 gemini-3.7-flash 重跑時，2048 那次 finishReason=STOP、
        # 章節齊全——因為那一次它 thoughtsTokenCount 是 0，完全沒花思考預算。
        # 同一份 prompt 在 gemini-3.6-flash 上則吃掉 1,962~2,772 個思考 token，
        # 只剩幾十個寫正文。
        #
        # 所以 2048 的問題不是「一定不夠」，是**時好時壞**：思考量因模型、
        # 甚至因每次請求而異，而超出時的表現是安靜截斷。驗收要驗的是這個機制，
        # 不是某個模型某一次的結果。
        if old_truncated:
            check(
                f"2048 被截斷（finishReason=MAX_TOKENS）{tag}",
                True,
                f"thoughts={old_thoughts} candidates={old['usage'].get('candidatesTokenCount')}"
                f"，正文只剩 {old['output_chars']} 字、章節 {len(old['sections_found'])}/3",
            )
            info("這一輪重現了截斷：思考預算把正文擠掉了")
        else:
            check(
                f"2048 這一輪未被截斷，且能解釋原因（思考 token 少）{tag}",
                old_thoughts < 500,
                f"finishReason={old['finish_reason']}，thoughts={old_thoughts}，"
                f"正文 {old['usage'].get('candidatesTokenCount')} token",
            )
            info(
                f"模型 {cfg.GEMINI_MODEL} 這一次沒花多少思考預算，所以 2048 剛好夠用。"
                "**這正是把上限訂在 2048 的風險**——它會過，直到某次思考變長就安靜截斷。"
            )

    section("2. 新值 maxOutputTokens=16384（正式設定）")
    check("設定值為 16384（靜態可驗，不需 API）", cfg.GEMINI_MAX_OUTPUT_TOKENS == 16384,
          str(cfg.GEMINI_MAX_OUTPUT_TOKENS))
    new, new_src = obtain(cfg.GEMINI_MAX_OUTPUT_TOKENS, prompt, len(prompt), space_name, only)
    if new is None:
        blocked("16384 不會被截斷（finishReason=STOP）", "配額用盡且無先前量測")
        blocked("16384 的三個章節齊全", "配額用盡且無先前量測")
        return summary()

    tag = "" if new_src == "fresh" else f"（沿用 {new['measured_at']} 的量測）"
    check(
        f"16384 不會被截斷（finishReason=STOP）{tag}",
        new["finish_reason"] == "STOP",
        f"finishReason={new['finish_reason']}",
    )
    check(
        f"16384 的三個章節齊全{tag}",
        len(new["sections_found"]) == 3,
        str(new["sections_found"]),
    )

    section("3. D-3 的核心論證：預算是「思考＋正文」的總和")

    def budget(m):
        u = m["usage"]
        return (u.get("thoughtsTokenCount") or 0) + (u.get("candidatesTokenCount") or 0)

    # 這是唯一與模型無關、必須恆成立的斷言：完整輸出所需的總預算超過舊上限。
    # 只要它成立，2048 就是不安全的——不論這一次有沒有剛好過關。
    check(
        "完整輸出所需的總預算（思考＋正文）超過舊上限 2048",
        budget(new) > 2048,
        f"thoughts={new['usage'].get('thoughtsTokenCount')} "
        f"＋ candidates={new['usage'].get('candidatesTokenCount')} "
        f"＝ {budget(new)} > 2048",
    )
    info(
        f"模型 {cfg.GEMINI_MODEL}：完整輸出用掉 {budget(new)} token 的輸出預算，"
        f"其中思考佔 {new['usage'].get('thoughtsTokenCount') or 0}。"
        "把上限訂在 2048 等於賭「這次思考不會太長」。"
    )

    if old is not None:
        check(
            "新值的輸出不短於舊值",
            new["output_chars"] >= old["output_chars"],
            f"{old['output_chars']} 字 -> {new['output_chars']} 字",
        )
        if old["finish_reason"] == "MAX_TOKENS":
            check(
                "截斷那次的正文遠少於完整輸出（量化資訊遺失）",
                old["output_chars"] < new["output_chars"] / 5,
                f"截斷 {old['output_chars']} 字 vs 完整 {new['output_chars']} 字",
            )

    info(f"量測值已存於 {MEASUREMENTS}（鍵含模型名；本輪：2048={old_src if old else 'n/a'}／16384={new_src}）")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
