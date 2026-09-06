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
    # 佔位符規則：模型很容易在看到檔名後自行腦補圖片內容（例如從 error.png
    # 推論「這是一張錯誤畫面」並當成事實寫進摘要）。必須明確禁止。
    "對話中若出現 `[圖片：檔名（AI 未讀取內容）]` 這種標記，表示該處有一張你看不到的圖："
    "請照實說明「該則訊息附有圖片但未讀取內容」，"
    "**不要從檔名臆測圖片內容，也不要假裝看過**；同時不要因此忽略該則訊息的文字部分。"
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


#: 引用程式碼的規則。這段是承重牆——比照 _BASE_RULES 對圖片佔位符的處理：
#: 模型看到函式名就會腦補實作，看到片段就會當成整個專案，兩者都會產出
#: 「看起來有憑有據、實際上錯」的答案，而那比不回答更糟。
_CODE_RULES = """
引用程式碼時的規則（違反其中任何一條，這份草稿就是錯的）：

1. **只講你在上面片段裡真的看得到的程式碼。** 不要補完沒有貼出來的函式，
   不要推論「這裡應該還會呼叫 X」，不要從函式名稱臆測它的實作。
2. **每一個關於程式碼的說法都要附 `檔案路徑:行號`**，讓對方可以自己打開來對。
   行號用上面標註的行號，不要自己重新數。
3. **一定要講清楚這段程式碼是哪個環境的。** 例如「正式環境（main 分支，commit a3f91c2）
   的 app/services/session.py:88 是這樣寫的」。絕對不要在沒有指明環境的情況下說
   「程式碼是這樣寫的」——提問者關心的往往正是「正式環境到底跑的是哪一版」。
4. **不要跨環境混用。** 若上面同時有正式環境與 UAT 的片段，兩者的差異要分開講，
   不能把 UAT 的行為說成正式環境的行為。
5. **上面的片段是搜尋結果，不是整個專案。** 沒搜到不等於不存在。若片段不足以回答問題，
   就在回話中說明「我查了 X 分支的 A、B 檔案，沒有看到相關邏輯」，並問回去需要什麼資訊，
   不要用猜的補足。
6. 片段裡若出現 `«已遮蔽»`，那是被系統遮蔽的敏感值，不要臆測它的內容，
   也不要在回話中重述任何看起來像密鑰或密碼的字串。
"""


def _code_section(code_blocks: Optional[List[Dict[str, Any]]]) -> str:
    """組裝【參考專案原始碼】區塊。

    三種狀態必須讓模型分得出來：
      * 沒選專案 → 不要對程式碼做任何陳述
      * 選了但零命中 → 「我查了正式環境，沒找到」**是有用的回答**，不能沉默
      * 有命中 → 附環境、分支、commit、行號
    第二種最容易被實作成跟第一種一樣，那會讓使用者以為系統沒查。
    """
    if not code_blocks:
        return (
            "\n\n【參考專案原始碼】Viewer 未指定任何參考專案，"
            "因此你看不到任何程式碼。不要在回話中對程式碼實作做任何陳述。"
        )

    parts: List[str] = []
    for b in code_blocks:
        label = b.get("environment_label") or b.get("environment", "")
        header = (
            f"\n--- 專案「{b['project_name']}」／{label}（{b['environment']}）／"
            f"分支 {b['branch']} @ {b['commit_sha']}"
        )
        if b.get("commit_date"):
            header += f"（{str(b['commit_date'])[:10]}）"
        header += " ---"
        parts.append(header)

        if b.get("terms"):
            parts.append("搜尋關鍵字：" + "、".join(b["terms"]))
        for note in b.get("notes") or []:
            parts.append(f"※ {note}")

        hits = b.get("hits") or []
        if not hits:
            parts.append(
                "搜尋結果：以上述關鍵字在此分支中**沒有找到**相符的程式碼。"
                "（這代表「查過了但沒有」，不是「沒有查」——回話時可以照實這樣說。）"
            )
            continue
        for h in hits:
            parts.append(f"\n【{h['path']}:{h['start_line']}-{h['end_line']}】\n{h['text']}")

    return (
        "\n\n【參考專案原始碼】以下是 Viewer 指定的專案原始碼，"
        "讀自特定分支的特定 commit（不是他本機未提交的版本）。\n"
        + "\n".join(parts)
        + "\n"
        + _CODE_RULES
    )


def draft_reply_prompt(
    *,
    mention_text: str,
    mention_sender: str,
    space_name: str,
    thread_text: str,
    reference_blocks: List[Dict[str, Any]],
    code_blocks: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """組裝 Draft Reply prompt（七節）。

    輸出兩段：脈絡分析與可直接送出的回話。reference_blocks 是 Reference Space 的
    近期對話，存在的理由是「@ 提出的問題，答案經常不在提問的那個 Space 裡」（ADR-0003）。

    code_blocks 是同一個洞見再往前一步：有時答案不在任何 Space，而在程式碼裡
    （ADR-0006）。它一定帶著環境與 commit——PM 問的往往正是「正式環境到底跑哪一版」，
    答案沒有指明環境就等於沒有回答。
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

    code = _code_section(code_blocks)

    return f"""你的任務是幫一位工程師草擬「回覆別人 @ 他的那則訊息」的回話。

【被 @ 的訊息】
聊天室：{space_name}
提問者：{mention_sender}
內容：{mention_text}

【該討論串的完整對話】
{thread_text}
{refs}
{code}

【輸出格式】嚴格依下列兩個章節輸出，不要增加其他章節：

### 🧭 脈絡分析
- **發生什麼事**：（這個討論串在講什麼，提問者想知道什麼）
- **關鍵決策**：（已經定案的事）
- **未解問題**：（還沒有答案的部分，以及回話時要小心的地方）
- **程式碼佐證**：（若有查程式碼，寫明查的是哪個環境／分支／commit，以及關鍵的檔案:行號；沒查就寫「未查程式碼」，查了但沒相符結果就寫「查了 X 分支，無相符」）

### ✍️ 建議回話
（一段可以直接複製送出的回話。要求：\
用繁體中文；\
直接回答提問者的問題，不要客套開場；\
只寫你在上面資料中真的看得到的事實，沒有依據的數字、日期、人名一律不要寫；\
若資料不足以回答，就在回話中明確問回去缺什麼；\
不要出現「根據參考群組」這類提問者看不懂的內部說法。）

{_BASE_RULES}
"""
