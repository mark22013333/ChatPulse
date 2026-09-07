"""Prompt 組裝：摘要風格分歧（缺陷 D-4）與 Draft Reply（七節）。

缺陷 D-4：`SummarizeRequest.style` 定義後從未被讀取——UI 有下拉選單、API 有欄位、
行為不存在。這裡把三種風格實作為**輸出章節結構不同的三份 prompt**，而不是只在
同一份 prompt 後面加一句「請寫技術一點」——後者在實務上經常產出幾乎一樣的結果，
無法通過「切換摘要風格會產生不同結果」這條驗收條件。
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import config as cfg
from .errors import InvalidParameter
from .reply_profiles import ReplyGenerationOptions, tone_instruction, tone_label

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


def _short_time(value: str) -> str:
    """RFC3339 -> 給人看的短時間。

    切法與 `format_conversation()` 完全一致（同樣是 UTC、同樣切到分鐘），
    這樣〔涵蓋範圍〕寫的起迄時間與底下每一行的時間戳對得起來。
    兩邊各自格式化就會出現「範圍寫台北時間、內文寫 UTC」這種對不上的情況。
    """
    return (value or "")[:16].replace("T", " ")


def _context_section(
    context_blocks: List[Dict[str, Any]], coverage: str
) -> str:
    """組裝【對話脈絡】。

    **這一段是整個改動的重點，不是加分項。** 在此之前區塊標題寫死成
    「【該討論串的完整對話】」，而私訊底下只有一行——那是個假承諾：
    模型收到的訊號是「這段對話就只有這麼多，沒有更多脈絡了」，它不會說
    「我脈絡不足」，它會直接編。更糟的是若把多個 thread 的內容塞進同一個標題底下，
    錯誤會從「明顯的資訊不足」升級成「看起來很有脈絡的錯誤歸因」。

    所以每個區塊都要能表達三件事：**來源**（同一串／同一聊天室的扁平序列／
    別的討論串）、**邊界**（幾則、涵蓋哪段時間）、**錨點**（要回的是哪一則）。
    """
    if not context_blocks:
        return (
            "\n\n【對話脈絡】系統沒有取到任何脈絡訊息，你只看得到上面那則。"
            "**不要**假裝知道背景，回話時直接問回去缺什麼。"
        )

    total = sum(int(b.get("count") or 0) for b in context_blocks)
    stamps = [
        s
        for b in context_blocks
        for s in (b.get("time_range") or ("", ""))
        if s
    ]
    span = (
        f"{_short_time(min(stamps))} ~ {_short_time(max(stamps))}"
        if stamps
        else "時間不明"
    )

    head = [
        "\n\n【對話脈絡】",
        f"〔涵蓋範圍〕共 {total} 則，{span}。要回覆的那一則在下方以 ▶ 標記。",
        "這是系統能取到的全部；超出這個範圍的內容你看不到。"
        "資訊不足以回答時，請在回話中明確問回去缺什麼，**不要推測、不要填補**。",
    ]
    if coverage == "partial":
        head.append(
            "〔注意〕系統沒能取回這則訊息周圍的完整對話（它可能太舊了），"
            "以下脈絡**不保證連續**，判斷時要更保守。"
        )

    parts: List[str] = ["\n".join(head)]
    for b in context_blocks:
        rng = b.get("time_range") or ("", "")
        when = (
            f"，{_short_time(rng[0])} ~ {_short_time(rng[1])}"
            if rng[0] or rng[1]
            else ""
        )
        parts.append(f"\n--- {b.get('label') or '對話'}{when} ---")
        # 警語放在區塊「之前」而不是統一塞進 _BASE_RULES 最後：
        # 模型對就近的指令服從度較高，而這條警語擋的正是「把別串結論當本串事實」。
        if b.get("note"):
            parts.append(str(b["note"]))
        parts.append(str(b.get("text") or "（這個區塊沒有可讀的內容）"))

    return "\n".join(parts)


#: 「### ✍️ 建議回話」那一行。
#:
#: 容忍標題層級（##～####）與 emoji 有無，因為模型偶爾會改寫標題的裝飾。
#: 前端 `store/draft.ts` 有一份等價的 regex（`REPLY_HEADING`）做即時切分；
#: 兩邊都以這裡的輸出格式為準——`draft_reply_prompt` 定義了這個標題，
#: 所以解析它的規則也放在同一個模組，改格式時兩件事會在同一個檔案裡被看到。
_REPLY_HEADING_RE = re.compile(r"^#{2,4}[ \t]*.*建議回話.*$", re.MULTILINE)


@dataclass(frozen=True)
class DraftSections:
    """把模型產出的草稿切成「脈絡分析」與「建議回話」兩段。

    切開的理由是**潤稿只能碰建議回話**：脈絡分析裡有程式碼佐證、
    未解問題、脈絡涵蓋這些以證據為準的欄位，讓潤稿器去改那一段
    等於讓一個看不到證據的模型改寫證據陳述。
    """

    #: 建議回話標題**之前**的全部內容（含脈絡分析與它的標題）
    head: str
    #: 建議回話的標題那一行；找不到時是空字串
    reply_heading: str
    #: 建議回話的內容（不含標題）
    reply: str

    @property
    def found(self) -> bool:
        """有沒有真的找到建議回話章節。

        `False` 時呼叫端**不應該**潤稿——潤整篇會改到脈絡分析。
        這是降級情況（模型沒照輸出格式回），不是錯誤。
        """
        return bool(self.reply_heading)

    def reassemble(self, reply: str) -> str:
        """用新的建議回話內容重組完整草稿，其餘部分逐字保留。"""
        if not self.found:
            return reply
        body = reply.strip("\n")
        return f"{self.head}{self.reply_heading}\n{body}\n" if body else f"{self.head}{self.reply_heading}\n"


def split_draft(text: str) -> DraftSections:
    """依輸出格式的標題把草稿切成兩段。找不到標題時 `found` 為 `False`。"""
    raw = text or ""
    match = _REPLY_HEADING_RE.search(raw)
    if not match:
        return DraftSections(head="", reply_heading="", reply=raw)
    return DraftSections(
        head=raw[: match.start()],
        reply_heading=match.group(0),
        reply=raw[match.end():].lstrip("\n"),
    )


def _reply_style_section(options: Optional[ReplyGenerationOptions]) -> str:
    """組裝【回話風格】區塊（Reply Tone／Persona／自訂提示，ADR-0007）。

    ## 為什麼這一段放在輸出格式**之後**、`_BASE_RULES` 之前

    需求定的優先序是：

        1. ChatPulse 的事實與安全規則   ← 永不可被覆蓋
        2. 程式碼佐證規則               ← 永不可被覆蓋
        3. Viewer 這次明確的自訂要求
        4. Persona
        5. Tone
        6. 預設寫作偏好

    直覺的實作是「把 1、2 放最前面」，但那是錯的。`_context_section` 的
    註解已經記錄過這件事：**模型對就近的指令服從度較高**。把不可覆蓋的
    規則放在最前面，等於讓它離輸出最遠、讓 tone／persona 離輸出最近——
    正好把優先序做反。

    所以實際的順序是「弱的先講、強的後講」：風格區塊（3–6）放在這裡，
    `_BASE_RULES`（1）壓在整份 prompt 的最尾端，`_CODE_RULES`（2）則
    緊貼在程式碼片段之後（那是它作用的對象）。再加上這個區塊自己
    開頭的明文宣告，優先序在**語意上**與**位置上**都成立。

    ## 為什麼要明文宣告「這一段不改變事實」

    因為 persona 的來源是不可信任的第三方內容，而它已經被實測含有
    「遇到不知道的事情可以合理推測」這類授權（見 `core/personas.py`）。
    淨化管線會剔除那些條目，但淨化是比對規則、不是理解語意——
    總會有沒想到的表達方式。這段宣告是第四層防線：即使有指令漏進來，
    它出現的位置也已經被框定成「Viewer 的偏好資料」，
    而不是「系統給你的新規則」。
    """
    if options is None or options.is_empty():
        return ""

    parts: List[str] = [
        "\n\n【回話風格】以下只影響〈建議回話〉那一段的**用字與語氣**。",
        "它不影響〈脈絡分析〉——脈絡分析一律以證據為準、保持中立陳述。",
        "它也**不得**改變任何事實、結論、數字、日期、人名、程式碼佐證，"
        "或讓任何一件該回的事被省略。風格與事實衝突時，一律以事實為準。",
    ]

    # 3：Viewer 這次明確的要求 —— 排在 persona 與 tone 之前，
    # 因為「這一次」的指示應該勝過「平常的偏好」。
    custom = (options.custom_prompt or "").strip()
    if custom:
        parts.append(
            "\n〔本次自訂要求〕Viewer 針對這一則回話特別交代的事"
            "（優先於下面的 Persona 與口氣設定）：\n"
            f"{custom}"
        )

    # 4：Persona
    if options.persona is not None:
        parts.append(_persona_lines(options.persona))

    # 5：Tone
    if options.tone:
        parts.append(
            f"\n〔口氣〕{tone_label(options.tone)}。{tone_instruction(options.tone)}"
        )

    return "\n".join(parts)


def _persona_lines(persona: Any) -> str:
    """把已淨化的 PersonaProfile 轉成 prompt 片段。

    **這裡拿到的一定是結構化 profile，不是遠端原文。** 型別上只要求
    `to_prompt_dict()`（見 `reply_profiles.PersonaProfileLike`），
    而那個方法的回傳值已經過 `core/personas.py` 的四層淨化。

    措辭刻意寫成「參考…的表達習慣」而不是「你是…」：Persona 的定位是
    借用思考框架與表達方式來協助寫回覆，不是 roleplay identity。
    送進 Google Chat 的回話不可以自稱是別人——那是最終會被真人讀到的
    文字，冒名的代價由使用者承擔。
    """
    data = persona.to_prompt_dict() if hasattr(persona, "to_prompt_dict") else dict(persona)
    name = str(data.get("name") or "").strip()

    lines: List[str] = [
        f"\n〔Persona〕參考「{name}」的思考與表達習慣來寫這則回話。"
        if name
        else "\n〔Persona〕參考下列思考與表達習慣來寫這則回話。",
        "這是從公開資料提煉的風格參考，**不是**要你扮演這個人："
        "回話中不可以自稱是他、不可以用他的名義發言、不可以提到這個 Persona 的存在。",
    ]

    def _bullets(label: str, values: Any) -> None:
        items = [str(v).strip() for v in (values or []) if str(v).strip()]
        if items:
            lines.append(f"- {label}：" + "；".join(items))

    _bullets("思考方式", data.get("thinking_style"))
    _bullets("表達習慣", data.get("communication_style"))
    _bullets("要避開", data.get("avoid"))

    prefs = data.get("response_preferences") or {}
    if isinstance(prefs, dict) and prefs:
        hints: List[str] = []
        verbosity = prefs.get("verbosity")
        if verbosity == "low":
            hints.append("偏短")
        elif verbosity == "high":
            hints.append("可以寫得完整一些")
        elif verbosity == "medium":
            hints.append("長度中等")
        if prefs.get("prefer_examples"):
            hints.append("習慣用具體例子或類比")
        if prefs.get("prefer_concrete_language"):
            hints.append("偏好具體、可驗證的說法")
        if hints:
            lines.append("- 篇幅與偏好：" + "、".join(hints))

    return "\n".join(lines)


def draft_reply_prompt(
    *,
    anchor_text: str,
    mention_sender: str,
    space_name: str,
    space_type_label: str,
    context_blocks: List[Dict[str, Any]],
    coverage: str = "full",
    anchor_count: int = 1,
    reference_blocks: List[Dict[str, Any]],
    code_blocks: Optional[List[Dict[str, Any]]] = None,
    reply_options: Optional[ReplyGenerationOptions] = None,
) -> str:
    """組裝 Draft Reply prompt（七節）。

    輸出兩段：脈絡分析與可直接送出的回話。reference_blocks 是 Reference Space 的
    近期對話，存在的理由是「@ 提出的問題，答案經常不在提問的那個 Space 裡」（ADR-0003）。

    code_blocks 是同一個洞見再往前一步：有時答案不在任何 Space，而在程式碼裡
    （ADR-0006）。它一定帶著環境與 commit——PM 問的往往正是「正式環境到底跑哪一版」，
    答案沒有指明環境就等於沒有回答。

    `context_blocks` 取代了原本的 `thread_text: str`（見 `_context_section`）。
    `anchor_text` 可能是**多則**，有兩種來源：
      1. 同一人的一串連發——私訊常把一個問題拆三則發，只印最後一則會讓模型
         照那句客套話回（見 `core/draft_context._collect_anchor_run`）。
      2. 使用者在收件匣多選了 N 則要「一起回」（`anchor_count > 1`）。
    兩者對模型的要求不同：第 1 種本來就是一個問題；第 2 種是 N 個各自獨立的
    問題，必須**明確要求用一則回話全部回完**，否則模型只會回最後看到的那個。

    `reply_options`（ADR-0007）帶 Reply Tone／Persona／自訂提示。
    **省略或為空時，產出的 prompt 與這個參數存在之前逐字相同**——
    這條由 `tests/unit/test_reply_prompt_options.py` 的向後相容測試守住，
    因為「不選任何回覆設定」是預設狀態，它不該讓既有行為改變。
    區塊為什麼放在輸出格式之後見 `_reply_style_section`。
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
        # 不要寫「僅依討論串本身作答」——私訊根本沒有討論串，那句話會讓模型
        # 以為自己漏看了什麼，或反過來把扁平序列腦補成一個討論串
        refs = "\n\n【參考聊天室的脈絡】Viewer 未指定任何參考聊天室，僅依上面的對話脈絡作答。"

    code = _code_section(code_blocks)
    context = _context_section(context_blocks, coverage)
    style = _reply_style_section(reply_options)

    # 多錨點：這 N 則是**各自獨立**的問題，只是要用一則回話回完。
    # 不講清楚的話模型會只回最後看到的那則——而且看起來完全正常，
    # 使用者要逐則比對才會發現有一題沒被回到。
    if anchor_count > 1:
        task = f"回覆別人在同一個對話裡問他、而他還沒回的 {anchor_count} 件事"
        header = f"【要回覆的訊息（{anchor_count} 件事，要用一則回話全部回完）】"
        multi = (
            f"\n※ 上面是 {anchor_count} 件**各自獨立**的事，不是同一個問題被拆開。"
            "它們的共同點是「對方問了、而你到現在一則都還沒回」，"
            "所以**每一件都要在這則回話裡處理掉，一件都不能漏**。"
            "\n※ 其中某一件現在真的答不了時，不可以只寫「我再看看」「我另外看」"
            "就帶過——那等於沒回。要寫出**缺什麼**（需要對方提供什麼）"
            "或**你要去確認什麼**（你打算查哪裡），讓對方知道下一步卡在誰身上。"
        )
    else:
        task = "回覆別人 @ 他的那則訊息"
        header = "【被 @ 的訊息】"
        multi = ""

    return f"""你的任務是幫一位工程師草擬「{task}」的回話。

{header}
聊天室：{space_name}（{space_type_label}）
提問者：{mention_sender}
內容：
{anchor_text}{multi}
{context}
{refs}
{code}

【輸出格式】嚴格依下列兩個章節輸出，不要增加其他章節：

### 🧭 脈絡分析
- **發生什麼事**：（這段對話在講什麼，提問者想知道什麼）
{"- **逐則確認**：（上面每一件事各自問了什麼、你在〈建議回話〉的哪一句回到它。一件一行，不可省略；答不了的要寫出缺什麼或你要去確認什麼）" + chr(10) if anchor_count > 1 else ""}\
- **關鍵決策**：（已經定案的事）
- **未解問題**：（還沒有答案的部分，以及回話時要小心的地方）
- **脈絡涵蓋**：（你實際看到的是哪個範圍、有沒有明顯缺口。這一欄不可省略——\
「脈絡不足」必須是你正面回答的事，不是可以沉默略過的事）
- **程式碼佐證**：（若有查程式碼，寫明查的是哪個環境／分支／commit，以及關鍵的檔案:行號；沒查就寫「未查程式碼」，查了但沒相符結果就寫「查了 X 分支，無相符」）

### ✍️ 建議回話
（一段可以直接複製送出的回話。要求：\
用繁體中文；\
直接回答提問者的問題，不要客套開場；\
只寫你在上面資料中真的看得到的事實，沒有依據的數字、日期、人名一律不要寫；\
若資料不足以回答，就在回話中明確問回去缺什麼；\
若脈絡顯示這個問題已經被別人回答了，**仍然要寫出你自己的回話**，\
並在〈脈絡分析〉的「未解問題」註明已被誰回答——不要把回話寫成「看起來 XXX 已經回覆了」；\
不要出現「根據參考群組」這類提問者看不懂的內部說法。）{style}

{_BASE_RULES}
"""
