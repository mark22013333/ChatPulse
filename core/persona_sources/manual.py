"""ManualPersonaSource：使用者自己填寫的 persona。

沒有網路、沒有 provenance 需要固定——但**內容一樣要走完整的淨化流程**。

理由很實際：使用者最可能的「手動填寫」方式，就是從某個地方複製一份
skill 全文貼進來。那份全文與從 GitHub 抓下來的沒有任何差別，
所以它必須走同一條 `personas.normalize_persona()` 或 `personas._coerce()`。

這個類別存在的價值是讓「手動」與「遠端」在 API 與 repository 層是
同一條路徑，只有 `source_type` 不同。少一條路徑就少一個「這條路徑
忘記淨化」的失敗形態。
"""

import hashlib
import json
from typing import Any, Dict, List, Optional

from ..errors import InvalidParameter
from .base import FetchedPersona, PersonaSource


class ManualPersonaSource(PersonaSource):
    """使用者手動提供的 persona。"""

    name = "manual"
    label = "自訂 Persona"

    def list_personas(self, **_: Any) -> List[Dict[str, Any]]:
        """手動來源沒有可列舉的目錄。

        回空陣列而不是拋錯：「這個來源不支援列舉」是正常狀態，
        不是故障（見 `PersonaSource.list_personas` 的契約）。
        """
        return []

    def fetch(
        self,
        *,
        name: str = "",
        description: str = "",
        raw_text: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> FetchedPersona:
        """把手填內容包成 `FetchedPersona`。

        兩種填寫方式：
          * `raw_text` — 貼一段 markdown，走與遠端相同的章節抽取
          * `profile` — 直接給結構化欄位（thinking_style 等），走 `_coerce`

        兩者都不可信任，差別只在走哪一條淨化入口。`profile` 會被序列化成
        JSON 存進 `raw_text`，這樣「原始輸入」永遠有一份可以回溯——
        使用者事後問「我當初填了什麼」時答得出來。
        """
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise InvalidParameter("自訂 Persona 需要名稱")

        if raw_text is None and profile is None:
            raise InvalidParameter(
                "自訂 Persona 需要 raw_text（一段風格描述）或 profile（結構化欄位）"
            )

        if raw_text is not None:
            payload = str(raw_text)
        else:
            payload = json.dumps(profile, ensure_ascii=False, sort_keys=True)

        return FetchedPersona(
            raw_text=payload,
            source_type=self.name,
            name_hint=cleaned_name,
            description_hint=(description or "").strip(),
            source_hash="sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            extra={"structured": profile is not None},
        )
