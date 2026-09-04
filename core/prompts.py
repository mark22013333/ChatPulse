"""Prompt 組裝：摘要風格分歧（缺陷 D-4）與 Draft Reply（七節）。

缺陷 D-4：`SummarizeRequest.style` 定義後從未被讀取——UI 有下拉選單、API 有欄位、
行為不存在。這裡把三種風格實作為**輸出章節結構不同的三份 prompt**，而不是只在
同一份 prompt 後面加一句「請寫技術一點」——後者在實務上經常產出幾乎一樣的結果，
無法通過「切換摘要風格會產生不同結果」這條驗收條件。
"""

from typing import Any, Dict, List, Optional

from . import config as cfg
from .errors import InvalidParameter

_BASE_RULES = (
    "請使用繁體中文輸出，並過濾掉純打招呼、貼圖、「收到」這類無實質內容的訊息。"
    "不要臆測對話中沒有出現的資訊；沒有的段落就寫「本次對話中未出現」。"
)

_STYLE_PROMPTS: Dict[str, Dict[str, str]] = {
    "general": {
        "label": "通用",
        "instruction": (
            "你是一位企業專案溝通分析師。請產出一份給「沒有跟到這段對話的人」也能看懂的摘要，"
            "重點放在事情的來龍去脈與結論。\n\n"
            "【輸出格式】嚴格依下列章節，不要增加其他章節：\n"
            "### 📌 核心討論主題與脈絡\n"
            "（分點說明討論了哪些事、為什麼會討論到、目前進展到哪）\n\n"
            "### 🤝 共識與重要決議\n"
            "（條列已拍板的事；若無請寫「尚在討論中」）\n\n"
            "### 🎯 待辦事項與追蹤 (Action Items)\n"
            "（每行格式務必精確：`• [負責人或組別] 具體任務內容`）\n"
        ),
    },
    "technical": {
        "label": "技術細節",
        "instruction": (
            "你是一位資深技術主管，讀者是要接手排查的工程師。請聚焦技術事實，"
            "把非技術的行政討論壓到最短。\n\n"
            "【輸出格式】嚴格依下列章節，不要增加其他章節：\n"
            "### 🔧 技術問題與症狀\n"
            "（錯誤訊息、發生條件、影響範圍；有的話逐字保留錯誤字串與代碼）\n\n"
            "### 🧪 已排除的假設與排查過程\n"
            "（誰試過什麼、結果如何、據此排除了什麼——這段是為了讓接手者不重複做白工）\n\n"
            "### 🛠️ 技術方案與取捨\n"
            "（提出過的做法、各自代價、目前傾向；含版本號、參數、設定檔路徑等具體物）\n\n"
            "### ⚠️ 未解的技術問題\n"
            "（仍然沒有答案的部分，以及缺什麼資訊才能判斷）\n\n"
            "### 🎯 待辦事項與追蹤 (Action Items)\n"
            "（每行格式務必精確：`• [負責人或組別] 具體任務內容`）\n"
        ),
    },
    "action_only": {
        "label": "只要待辦",
        "instruction": (
            "你是一位專案助理。**只輸出待辦事項，不要輸出任何摘要、脈絡說明或前言。**\n\n"
            "【輸出格式】只輸出下列一個章節：\n"
            "### 🎯 待辦事項與追蹤 (Action Items)\n"
            "（每行格式務必精確：`• [負責人或組別] 具體任務內容`。"
            "若能從對話判斷期限就在任務後補 `（期限：…）`；判斷不出來就不要寫。）\n\n"
            "若整段對話沒有任何待辦，就只輸出一行：`• 本次對話中未出現明確待辦事項`。"
            "不要為了湊數把一般討論寫成待辦。"
        ),
    },
}


def validate_style(style: Optional[str]) -> str:
    """回傳合法的風格值；不合法時拋 INVALID_PARAMETER（8.4）。"""
    value = (style or cfg.SUMMARY_STYLE_DEFAULT).strip()
    if value not in _STYLE_PROMPTS:
        raise InvalidParameter(
            f"style 只接受 {'／'.join(cfg.SUMMARY_STYLES)}，收到 {style!r}"
        )
    return value


def style_options() -> List[Dict[str, str]]:
    """給前端下拉選單用的選項清單。"""
    return [{"value": k, "label": v["label"]} for k, v in _STYLE_PROMPTS.items()]


def summary_prompt(
    space_name: str, conversation_text: str, message_count: int, style: str
) -> str:
    """組裝單群摘要 prompt。style 已由 validate_style 驗過。"""
    instruction = _STYLE_PROMPTS[style]["instruction"]
    return (
        f"{instruction}\n{_BASE_RULES}\n\n"
        f"【分析對象】Google Chat 聊天室「{space_name}」最近 {message_count} 則對話\n\n"
        f"【對話紀錄】\n{conversation_text}\n\n"
        "請開始輸出："
    )


def draft_reply_prompt(
    *,
    mention_text: str,
    mention_sender: str,
    space_name: str,
    thread_text: str,
    reference_blocks: List[Dict[str, Any]],
) -> str:
    """組裝 Draft Reply prompt（七節）。

    輸出兩段：脈絡分析與可直接送出的回話。reference_blocks 是 Reference Space 的
    近期對話，存在的理由是「@ 提出的問題，答案經常不在提問的那個 Space 裡」（ADR-0003）。
    """
    refs = ""
    if reference_blocks:
        parts = []
        for block in reference_blocks:
            parts.append(
                f"\n--- 參考聊天室「{block['space_name']}」最近 {block['message_count']} 則 ---\n"
                f"{block['conversation_text']}"
            )
        refs = (
            "\n\n【參考聊天室的脈絡】以下是 Viewer 手動指定的其他聊天室內容。"
            "被 @ 的那個聊天室裡通常沒有答案，答案在這裡。"
            "引用這些內容時要注意：提問者看不到這些聊天室，所以回話中要把必要背景講清楚，"
            "不能寫成「如同 X 群組說的」。\n" + "\n".join(parts)
        )
    else:
        refs = "\n\n【參考聊天室的脈絡】Viewer 未指定任何參考聊天室，僅依討論串本身作答。"

    return f"""你的任務是幫一位工程師草擬「回覆別人 @ 他的那則訊息」的回話。

【被 @ 的訊息】
聊天室：{space_name}
提問者：{mention_sender}
內容：{mention_text}

【該討論串的完整對話】
{thread_text}
{refs}

【輸出格式】嚴格依下列兩個章節輸出，不要增加其他章節：

### 🧭 脈絡分析
- **發生什麼事**：（這個討論串在講什麼，提問者想知道什麼）
- **關鍵決策**：（已經定案的事）
- **未解問題**：（還沒有答案的部分，以及回話時要小心的地方）

### ✍️ 建議回話
（一段可以直接複製送出的回話。要求：\
用繁體中文；\
直接回答提問者的問題，不要客套開場；\
只寫你在上面資料中真的看得到的事實，沒有依據的數字、日期、人名一律不要寫；\
若資料不足以回答，就在回話中明確問回去缺什麼；\
不要出現「根據參考群組」這類提問者看不懂的內部說法。）

{_BASE_RULES}
"""
