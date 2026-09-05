"""E2E：訊息附件（圖片）的處理。

對應規格：六節（Draft Reply 的脈絡）、D-7（發言者名錄）同族的「資料取了卻沒用」問題。

**這一套要守的核心行為**：在此之前 `format_conversation()` 只取 `text`，
於是工作群組裡最常見的「@某人 ＋ 一張截圖」在模型眼中只剩下那個 @，
而「只有圖、沒有文字」的訊息會**整則消失**且毫無跡象。

分成兩段：結構化樣本（可無限重跑、涵蓋真實資料掃不到的分支）
與真實 API 樣本（證明欄位在使用者驗證下真的有值，不是只有理論成立）。
不呼叫任何 AI，不消耗配額。
"""

import os
import sys

# 專案根＝tests/e2e 往上兩層。不寫死絕對路徑，同事 clone 到別的位置也要能跑
E2E_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(E2E_DIR)))
sys.path.insert(0, E2E_DIR)

from core import prompts  # noqa: E402
from core.chat_client import (  # noqa: E402
    GoogleChatClient,
    attachment_note,
    format_conversation,
)
from e2e_lib import (  # noqa: E402
    TEMP_SPACE,
    blocked,
    check,
    info,
    section,
    set_report,
    summary,
)

REPORT = os.environ.get(
    "E2E_REPORT",
    os.path.join(E2E_DIR, "reports", "e2e-attachments.md"),
)


def _msg(text=None, attachment=None, sender="users/111", created="2026-09-04T14:22:00Z"):
    m = {"sender": {"name": sender}, "createTime": created}
    if text is not None:
        m["text"] = text
    if attachment is not None:
        m["attachment"] = attachment
    return m


IMG = {"contentName": "error.png", "contentType": "image/png",
       "attachmentDataRef": {"resourceName": "abc"}}
# 實測形態：同樣是 Chat 上傳，這一筆完全沒有 source 欄位
IMG_NO_SOURCE = {"contentName": "shot.jpg", "contentType": "image/jpeg",
                 "attachmentDataRef": {"resourceName": "def"}}
PDF = {"contentName": "spec.pdf", "contentType": "application/pdf",
       "attachmentDataRef": {"resourceName": "ghi"}}
DRIVE = {"contentName": "設計稿", "contentType": "application/vnd.google-apps.folder",
         "driveDataRef": {"driveFileId": "xyz"}, "source": "DRIVE_FILE"}


def main() -> int:
    set_report(REPORT)

    # ------------------------------------------------------------------
    section("1. 佔位符組裝（結構化樣本，可無限重跑）")

    check("沒有附件時回空字串", attachment_note(_msg("嗨")) == "", repr(attachment_note(_msg("嗨"))))
    check("attachment 為空陣列也回空字串", attachment_note(_msg("嗨", [])) == "")

    one = attachment_note(_msg("這個錯誤怎麼解", [IMG]))
    check("單張圖片標出檔名與「未讀取」", "error.png" in one and "未讀取" in one, one)

    multi = attachment_note(_msg("三張", [IMG, IMG_NO_SOURCE]))
    check("多張圖片標出張數", "×2" in multi and "error.png" in multi and "shot.jpg" in multi, multi)

    # 防禦性測試：本帳號實測 84 個附件都有 source，但 Google API 慣例會省略
    # enum 預設值，且這個保證不寫在文件裡。缺欄位時不該退化成「不是圖片」。
    no_src = attachment_note(_msg("看圖", [IMG_NO_SOURCE]))
    check(
        "就算沒有 source 欄位，圖片仍靠 contentType 被認出",
        "shot.jpg" in no_src and "圖片" in no_src,
        no_src,
    )

    pdf_note = attachment_note(_msg("文件", [PDF]))
    check("非圖片附件標為附件而不是圖片", "附件" in pdf_note and "圖片" not in pdf_note, pdf_note)

    mixed = attachment_note(_msg("都有", [IMG, PDF]))
    check("圖片與非圖片混合時兩者都列出", "error.png" in mixed and "spec.pdf" in mixed, mixed)

    drive_note = attachment_note(_msg("雲端", [DRIVE]))
    check("Drive 來源的非圖片附件也列得出來", "設計稿" in drive_note, drive_note)

    nameless = attachment_note(_msg("無名", [{"contentType": "image/png"}]))
    check("沒有 contentName 時不會產出空檔名", "未命名檔案" in nameless, nameless)

    # ------------------------------------------------------------------
    section("2. 組進對話文本（這是修復的重點）")

    convo = format_conversation([_msg("@李四 這個錯誤怎麼解", [IMG])], lambda uid: "王小明")
    check("有文字＋有圖：兩者都在", "這個錯誤怎麼解" in convo and "error.png" in convo, convo)

    # 修復前的行為：這一則會整個不見（因為 `if text:` 擋掉了）
    only_img = format_conversation([_msg(None, [IMG])], lambda uid: "王小明")
    check(
        "只有圖、沒有文字的訊息不再整則消失（修復前會）",
        bool(only_img.strip()) and "error.png" in only_img,
        only_img or "(空字串——這就是修復前的行為)",
    )

    empty_text = format_conversation([_msg("   ", [IMG])], lambda uid: "王小明")
    check("文字只有空白但有圖時仍保留該則", "error.png" in empty_text, empty_text)

    # 負對照：沒有附件的訊息，輸出必須跟以前一模一樣（不能有多餘空白或標記）
    plain = format_conversation([_msg("純文字訊息")], lambda uid: "王小明")
    check(
        "沒有附件的訊息輸出不受影響（負對照）",
        plain == "[2026-09-04 14:22] 王小明: 純文字訊息",
        repr(plain),
    )

    nothing = format_conversation([_msg(None)], lambda uid: "王小明")
    check("既沒文字也沒附件的訊息仍然被略過（負對照）", nothing == "", repr(nothing))

    # ------------------------------------------------------------------
    section("3. Prompt 規則")

    for style in ("general", "technical", "action_only"):
        p = prompts.summary_prompt(
            "測試群",
            "[2026-09-04 10:00] 甲: 看圖 [圖片：a.png（AI 未讀取內容）]",
            10,
            style,
        )
        check(
            f"{style} 風格的 prompt 含佔位符處理規則",
            "未讀取內容" in p and "臆測" in p,
            "有規則" if "未讀取內容" in p else "缺規則",
        )

    # ------------------------------------------------------------------
    section("4. 真實 API 樣本（證明欄位在使用者驗證下真的有值）")

    try:
        client = GoogleChatClient(interactive=False)
    except Exception as exc:
        blocked("真實 API 樣本", f"無法建立 Chat client：{exc}")
        return summary()

    data = client._request(
        "GET", f"{TEMP_SPACE}/messages",
        params={"pageSize": 100, "orderBy": "createTime desc"},
    )
    msgs = data.get("messages", [])
    with_att = [m for m in msgs if m.get("attachment")]
    info(f"0.暫存 最近 {len(msgs)} 則中有 {len(with_att)} 則帶附件")

    check(
        "使用者驗證下 attachment 欄位確實會被填",
        bool(with_att),
        f"{len(with_att)} 則帶附件",
    )

    if with_att:
        images = [
            a for m in with_att for a in m["attachment"]
            if (a.get("contentType") or "").startswith("image/")
        ]
        check("其中有圖片類附件", bool(images), f"{len(images)} 個圖片附件")

        # 記錄實際分布：用來支撐「不拿 source 當圖片判準」這個決定的依據
        sources = {}
        for a in images:
            sources[a.get("source", "(缺欄位)")] = sources.get(a.get("source", "(缺欄位)"), 0) + 1
        info(f"圖片附件的 source 分布：{sources}（分類仍以 contentType 為準）")

        has_ref = [a for a in images if a.get("attachmentDataRef", {}).get("resourceName")]
        check(
            "圖片附件帶得出 attachmentDataRef.resourceName（下載的依據）",
            len(has_ref) == len(images),
            f"{len(has_ref)}/{len(images)}",
        )

        real = format_conversation(with_att[:5], lambda uid: f"成員…{(uid or '')[-4:]}")
        check("真實訊息組出的對話文本含圖片佔位符", "圖片：" in real, real[:120])
        info(f"實際輸出範例：{real.splitlines()[0][:100] if real else '(空)'}")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
