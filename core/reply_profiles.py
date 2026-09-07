"""Reply Tone：Draft Reply 的「回覆口氣」表，以及一次草稿的寫作偏好組合。

**這裡跟 `prompts._STYLE_PROMPTS`（Summary Style）是兩回事，不要混用。**
Summary Style 決定「摘要輸出哪些章節」——它改的是**結構**；
Reply Tone 決定「同一段事實用什麼語氣寫出來」——它改的是**用字**，
而且只作用在 `### ✍️ 建議回話` 那一段（見 `prompts.draft_reply_prompt`）。

兩者都存在的理由是它們會同時被使用：一個 Viewer 可以用 `technical` 風格看摘要、
用 `engineer` 口氣寫回話。把它們合併成一個「AI 風格」設定會讓兩邊互相污染，
所以 config 的常數、DB 的欄位、API 的欄位全部分開命名（`SUMMARY_STYLE_*` 對
`REPLY_TONE_*`、`default_style` 對 `default_reply_tone`）。

## Tone 能改什麼、不能改什麼

能改：用字、句子長短、正式程度、直接程度、禮貌程度、技術表達方式。
不能改：事實、結論、程式碼證據、日期、數字、人物、Reference Space 的內容。

「production branch 的 timeout 是 30 秒」這件事，professional 可以寫成
「目前 production branch 的 timeout 設定為 30 秒」，engineer 可以寫成
「我看 production branch，目前 timeout 是 30 秒」——但沒有任何一個 tone
可以把 30 秒寫成 60 秒。這條界線由 `prompts` 層的區塊順序與 `_BASE_RULES`
共同守住（tone 指示放在前面、事實規則放在最尾端，就近原則讓後者更強）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import InvalidParameter

#: 每個 tone 的 `example` 都在描述同一個事實：
#: 「production branch 的 timeout 是 30 秒」。
#: 這是刻意的——UI 把這些範例並排顯示時，使用者一眼就看得出
#: **變的是語氣、不是內容**，不需要為了預覽去打一次 AI（省 token，
#: 而且固定範例比即時產生的預覽更能表達「tone 不會改事實」這條保證）。
_TONES: Dict[str, Dict[str, str]] = {
    "natural": {
        "label": "自然直接",
        "description": "像平常在對話裡講話，不刻意正式也不刻意親切",
        "instruction": (
            "用平常在聊天室裡跟同事講話的口氣：完整句子，但不用書面語。"
            "可以用「我看了一下」「這邊」這類口語連接，不要用「謹此」「敬請」這類公文語。"
        ),
        "example": "我看了一下，production 現在的 timeout 是 30 秒。",
    },
    "professional": {
        "label": "專業正式",
        "description": "對外、對主管或跨部門溝通用的正式書面語",
        "instruction": (
            "用正式的書面中文：完整主詞與動詞，避免省略與口語縮寫，"
            "陳述句為主。不要用表情符號，不要用「啦」「喔」「欸」這類語尾助詞。"
            "保持禮貌但不寒暄——正式不等於客套。"
        ),
        "example": "目前 production branch 的 timeout 設定為 30 秒。",
    },
    "concise": {
        "label": "簡潔明確",
        "description": "只講結論與必要細節，能一句講完就不用兩句",
        "instruction": (
            "把長度壓到最短：先給答案，再給必要的依據，其餘全部刪掉。"
            "不要鋪陳、不要重述問題、不要加結尾句。"
            "能用一個短句講完的事就不要寫成一段。"
        ),
        "example": "production timeout 目前是 30 秒。",
    },
    "friendly": {
        "label": "親切友善",
        "description": "語氣溫和好接近，適合對新人或跨團隊的人",
        "instruction": (
            "語氣放軟、讓人好接話：可以用一句短的招呼或確認語，"
            "把「你應該」換成「我們可以」。但**不要**變成客服式罐頭語句"
            "（不要出現「感謝您的來信」「希望對您有幫助」這類句子），"
            "也不要因為想友善就把壞消息說得模糊。"
        ),
        "example": "我幫你看了一下，production 目前 timeout 是設 30 秒喔。",
    },
    "engineer": {
        "label": "工程師協作",
        "description": "像工程師與工程師之間討論問題，直接、具體、不官腔",
        "instruction": (
            "當成在跟另一個工程師講話：第一句就給結論或答案，理由放後面。"
            "指到具體的東西——分支、環境、檔案:行號、錯誤訊息原文、參數值。"
            "不同意就直接說不同意並給理由，不要包在客套裡。"
            "不確定就直接說不確定，不要用三段模糊的話來掩飾。"
            "術語照工程慣例寫，不要翻譯成不精確的中文。"
        ),
        "example": "我看 production branch，目前 timeout 是 30 秒（TimeoutConfig.java:88）。",
    },
    "soft": {
        "label": "委婉柔和",
        "description": "需要拒絕、指出問題或談敏感事情時，降低對抗感",
        "instruction": (
            "在不改變結論的前提下降低對抗感：把否定改成條件句"
            "（「這樣做會撞到 X」而不是「你錯了」），把要求改成詢問。"
            "**結論本身不可以被軟化到讓對方讀不出來**——"
            "如果答案是不行，讀完必須知道是不行，不是「看起來也許有機會」。"
        ),
        "example": "這邊看到 production 的 timeout 目前是 30 秒，可能跟預期的不太一樣？",
    },
    "assertive": {
        "label": "堅定明確",
        "description": "需要拍板、擋掉發散討論或給出明確指示時",
        "instruction": (
            "明確表態並承擔判斷：用「我建議」「我們就照 X 做」這類句式，"
            "不要用「或許可以考慮」「不妨評估一下」把責任推回去。"
            "該說「這個不做」就說不做並給一個理由。"
            "仍然只能對看得到的事實下判斷——堅定講的是語氣，不是把推測講得很有信心。"
        ),
        "example": "production 的 timeout 就是 30 秒，先照這個值來對，不用再猜。",
    },
    "custom": {
        "label": "自訂",
        "description": "不套用任何內建語氣，完全依你在「自訂提示」寫的要求",
        "instruction": (
            "不套用任何預設語氣模板。語氣、長度與用字完全依 Viewer 在"
            "〔本次自訂要求〕中的指示；若他沒有寫語氣相關的要求，"
            "就用中性、平實的書面中文，不要自行選一種風格。"
        ),
        "example": "（依你自己寫的自訂提示決定）",
    },
}

#: 沒有指定 tone 時用哪一個。選 natural 而不是 engineer，理由是
#: 「不選」應該得到最接近現況的行為——`natural` 的 instruction 最接近
#: 這個功能加進來之前 prompt 本來的語氣要求（見 ADR-0007 的向後相容一節）。
REPLY_TONE_DEFAULT = "natural"

#: 合法 tone 的單一事實來源。config 不另列一份（比照 `cfg.AI_PROVIDER`
#: 對 `providers.VALID_NAMES` 的處理），避免兩邊漂移。
REPLY_TONES = tuple(_TONES)


def validate_tone(tone: Optional[str]) -> str:
    """回傳合法的 tone id；`None`／空白字串退回預設值，不合法則拋 INVALID_PARAMETER。

    空字串視為「沒有偏好」而不是非法值——這與 `repository.get_preferences()`
    對 `default_provider` 的處理一致（空字串 → 退回伺服器預設）。

    先 strip 再判斷 falsy，順序不能顛倒。寫成 `(tone or DEFAULT).strip()` 的話
    `"   "` 是 truthy、不觸發 fallback，strip 完變成 `""` 再去查表就落空——
    於是 `""` 降級成預設值、`"   "` 卻拋 400，而兩者 strip 之後語意完全相同。
    """
    value = (tone or "").strip() or REPLY_TONE_DEFAULT
    if value not in _TONES:
        raise InvalidParameter(
            f"tone_id 只接受 {'／'.join(REPLY_TONES)}，收到 {tone!r}"
        )
    return value


def tone_options() -> List[Dict[str, str]]:
    """給前端下拉選單用的選項清單。

    **刻意不含 `instruction`。** 那是送給模型的 prompt 片段，前端不需要它，
    送出去只會變成「使用者可以讀到、卻不能改」的死資料，還會讓
    prompt 內容出現在瀏覽器 devtools 裡。`example` 有送——UI 要拿它做
    固定預覽（見 `_TONES` 的註解）。
    """
    return [
        {
            "id": key,
            "label": value["label"],
            "description": value["description"],
            "example": value["example"],
        }
        for key, value in _TONES.items()
    ]


def tone_label(tone: Optional[str]) -> str:
    """tone id → 繁中標籤；認不出來就回原字串（不要在顯示路徑上拋錯）。"""
    value = (tone or "").strip()
    entry = _TONES.get(value)
    return entry["label"] if entry else value


def tone_instruction(tone: str) -> str:
    """tone id → 送給模型的語氣指示。tone 必須已經過 `validate_tone`。"""
    return _TONES[tone]["instruction"]


@dataclass(frozen=True)
class ReplyGenerationOptions:
    """一次 Draft Reply 的「寫作偏好」——已經解析完、可以直接餵進 prompt 的形狀。

    這個 dataclass 的存在理由是**把「解析」與「組裝」切開**：
    API 層負責處理「per-draft 覆寫 > Viewer 偏好 > 系統預設」這條優先序、
    驗證 tone 值、查 persona 並驗擁有權、決定 custom prompt 從哪來；
    `prompts` 層只拿到一個已經確定的結果，不需要知道那些規則。

    這樣做的直接好處是 prompt 組裝變成純函式，可以用字串斷言窮盡驗證
    （比照既有的 `test_draft_prompt.py`，149 個測試 0.042 秒、不打任何 API）。

    欄位都有預設值，所以 `ReplyGenerationOptions()` 就是「什麼都沒選」，
    此時 `prompts.draft_reply_prompt` 產出的 prompt 與這個功能加進來之前
    **逐字相同**（由 `test_reply_profiles.py` 的向後相容測試守住）。
    """

    #: 已驗證的 tone id。`None` 代表連預設 tone 的指示都不要加進 prompt——
    #: 這是向後相容用的「完全不介入」狀態，與 `"natural"` 不同。
    tone: Optional[str] = None
    #: Viewer 這一次明確寫的要求（inline 或套用 preset 後的內容）。
    custom_prompt: Optional[str] = None
    #: 已經 normalize + sanitize 過的 persona。**不可以是遠端原文。**
    persona: Optional["PersonaProfileLike"] = None

    def is_empty(self) -> bool:
        """三者都沒有 → prompt 完全不需要加【回話風格】區塊。"""
        return not self.tone and not (self.custom_prompt or "").strip() and self.persona is None


#: `ReplyGenerationOptions.persona` 的鴨子型別。
#:
#: 刻意不 import `core.personas.PersonaProfile`：`reply_profiles` 是純資料模組，
#: 而 `personas` 需要 sanitize 邏輯與 schema 版本管理。讓前者依賴後者會讓
#: 「加一個 tone」這種小改動被迫連帶讀懂 persona 的整套淨化流程。
#: prompts 層只用到 `name` 與 `to_prompt_dict()`，這裡用 Protocol 表達這件事。
try:  # pragma: no cover - typing 專用，執行期沒有 Protocol 也要能跑
    from typing import Protocol

    class PersonaProfileLike(Protocol):
        """prompts 層對 persona 的唯一要求。"""

        name: str

        def to_prompt_dict(self) -> Dict[str, Any]:
            """回傳可以直接寫進 prompt 的結構化欄位。"""
            ...

except ImportError:  # pragma: no cover
    PersonaProfileLike = Any  # type: ignore[assignment,misc]
