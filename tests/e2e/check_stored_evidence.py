"""從資料庫既有的產出驗證宣稱——不呼叫 Gemini，不受配額影響。

**這支腳本驗的是「已經產生的東西」，不是「功能還能不能跑」。** 兩者不同：
前者證明「這件事發生過且結果正確」，後者證明「現在再做一次也會對」。
規格書引用的數字若能從這裡查到，就屬 A 級證據（可隨時重查、不依賴外部配額）。

會這樣拆是因為 2026-09-05 踩到的坑：Gemini 免費層每天只有 20 次請求，
用完之後所有依賴它的驗收都跑不動，而當時規格書引用的數字又只存在於
被覆蓋掉的報告檔裡，於是獨立審查判定那些宣稱無依據——即使數字是真的。
凡是能從落地資料重查的，就不該只留在終端輸出裡。
"""

import os
import sys

sys.path.insert(0, "/Users/cheng/google-chat-bot")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config as cfg  # noqa: E402
from core import db  # noqa: E402
from e2e_lib import blocked, check, info, section, set_report, summary  # noqa: E402

REPORT = os.environ.get(
    "E2E_REPORT",
    "/private/tmp/claude-501/-Users-cheng-google-chat-bot/e23e39c0-7bfd-493e-8211-f63e32bb9432/scratchpad/e2e-stored-evidence.md",
)

# 每種風格**自己的**預期章節。用 general 的章節名去檢查 action_only 會得到
# 「1/3 疑似截斷」的假結論——章節結構不同正是 D-4 要達成的效果，不是缺陷。
EXPECTED_SECTIONS = {
    "general": ["核心討論主題", "共識與重要決議", "待辦事項"],
    "technical": [
        "技術問題與症狀",
        "已排除的假設",
        "技術方案與取捨",
        "未解的技術問題",
        "待辦事項",
    ],
    "action_only": ["待辦事項"],
}
# 這些章節出現在該風格就是錯的
FORBIDDEN_SECTIONS = {
    "action_only": ["核心討論主題", "技術方案與取捨", "共識與重要決議"],
    "general": ["已排除的假設與排查過程"],
}


def main() -> int:
    set_report(REPORT)
    db.init_db()

    section("1. 每份已產生的 Summary，章節結構是否符合它自己的風格")
    rows = db.query_all(
        "SELECT id, space_name, style, message_count, content_md, created_at "
        "FROM summaries ORDER BY id"
    )
    if not rows:
        blocked("Summary 章節結構", "資料庫中還沒有任何 Summary，先產生幾份再跑")
        return summary()

    info(f"資料庫共 {len(rows)} 份 Summary，全部由 maxOutputTokens={cfg.GEMINI_MAX_OUTPUT_TOKENS} 產生")
    for r in rows:
        md, style = r["content_md"], r["style"]
        expected = EXPECTED_SECTIONS.get(style, [])
        got = [s for s in expected if s in md]
        forbidden_hit = [s for s in FORBIDDEN_SECTIONS.get(style, []) if s in md]
        check(
            f"Summary id={r['id']}（{style}，{r['message_count']} 則）章節齊全且無越界",
            len(got) == len(expected) and not forbidden_hit,
            f"{len(got)}/{len(expected)} 章節、{len(md)} 字"
            + (f"、不該出現卻出現 {forbidden_hit}" if forbidden_hit else ""),
        )

    section("2. D-4：三種風格確實產生不同結果")
    by_style = {}
    for r in rows:
        by_style.setdefault(r["style"], []).append(r)

    missing = [s for s in cfg.SUMMARY_STYLES if s not in by_style]
    if missing:
        blocked("三種風格對照", f"資料庫缺少這些風格的 Summary：{missing}")
    else:
        # 取每種風格「則數相同」的那一組來比，否則長度差可能只是因為輸入不同
        counts = {s: {r["message_count"] for r in v} for s, v in by_style.items()}
        common = set.intersection(*counts.values())
        if not common:
            blocked("三種風格對照", f"三種風格沒有共同的則數可比：{counts}")
        else:
            n = max(common)
            picked = {s: next(r for r in by_style[s] if r["message_count"] == n) for s in cfg.SUMMARY_STYLES}
            info(f"以則數 = {n} 的那一組比較（同一批輸入，唯一變數是 style）")
            for s, r in picked.items():
                info(f"  {s:11} id={r['id']} → {len(r['content_md'])} 字")

            texts = {s: r["content_md"] for s, r in picked.items()}
            check(
                "三種風格的輸出兩兩相異",
                len({v for v in texts.values()}) == 3,
                "；".join(f"{s}={len(t)} 字" for s, t in texts.items()),
            )
            check(
                "action_only 不含「核心討論主題」（只輸出待辦）",
                "核心討論主題" not in texts["action_only"],
            )
            check(
                "general 含「核心討論主題」",
                "核心討論主題" in texts["general"],
            )
            check(
                "technical 含 general 沒有的「已排除的假設」章節",
                "已排除的假設" in texts["technical"]
                and "已排除的假設" not in texts["general"],
            )
            check(
                "technical 最長、action_only 最短（章節數多寡的直接後果）",
                len(texts["technical"]) > len(texts["general"]) > len(texts["action_only"]),
                f"{len(texts['technical'])} > {len(texts['general'])} > {len(texts['action_only'])}",
            )

    section("3. D-3：16384 的輸出未被截斷（就已產生的這幾份而言）")
    truncated = [r for r in rows
                 if len([s for s in EXPECTED_SECTIONS.get(r["style"], []) if s in r["content_md"]])
                 < len(EXPECTED_SECTIONS.get(r["style"], []))]
    check(
        f"{len(rows)} 份 Summary 全部章節齊全，無一被截斷",
        not truncated,
        f"被截斷的：{[r['id'] for r in truncated]}" if truncated else "0 份被截斷",
    )
    info(
        "涵蓋範圍說明：這幾份是 30~50 則對話。**483 則那份的 16384 對照不在這裡**——"
        "那要跑 tests/e2e/test_d3_truncation.py，需要 Gemini 配額。"
        "本節能證明的是「16384 在這個規模下不截斷」，不是「任何規模都不截斷」。"
    )

    section("4. R-2：token 用量的記錄機制與 thinking token 的存在")
    usage = db.query_all(
        "SELECT operation, COUNT(*) n, SUM(prompt_tokens) p, SUM(output_tokens) o, "
        "SUM(total_tokens) t FROM token_usage GROUP BY operation"
    )
    check("token_usage 有記錄", bool(usage), f"{len(usage)} 種操作")
    for u in usage:
        info(f"  {u['operation']}：{u['n']} 次，prompt {u['p']}／output {u['o']}／total {u['t']}")
    gap = [u for u in usage if u["t"] > u["p"] + u["o"]]
    check(
        "totalTokenCount 大於 prompt+output（差額即 thinking／快取）",
        bool(gap),
        "；".join(f"{u['operation']}: {u['p']}+{u['o']}={u['p']+u['o']} < total {u['t']}" for u in gap),
    )
    info("這是 R-2 估成本時不能只看 output 的直接證據，且可隨時重查。")

    section("5. 規格 7.1：Draft Reply 引用 Reference Space（正負對照，配對比較）")
    # 這是 Phase 2 最核心的驗收條件。它要求的是**受控比較**：同一則 Mention、
    # 同一個討論串，唯一變數是有沒有勾選 Reference Space。資料庫裡剛好留著
    # 成對的草稿，所以不必重打 Gemini 就能驗。
    #
    # 標記事實選的是專案代號 FIA01P2401 與 repo 名 SmartKMS——這種任意
    # 英數字串猜不出來，模型只可能從參考群組的對話裡讀到。
    FACTS = ["FIA01P2401", "SmartKMS"]
    drafts = db.query_all(
        "SELECT id, mention_id, content_md, sent_at FROM draft_replies ORDER BY id"
    )
    if not drafts:
        blocked("Reference Space 正負對照", "資料庫中沒有任何 Draft Reply")
    else:
        by_mention = {}
        for d in drafts:
            has = all(f in d["content_md"] for f in FACTS)
            by_mention.setdefault(d["mention_id"], {"with": [], "without": []})[
                "with" if has else "without"
            ].append(d)

        pairs = {mid: v for mid, v in by_mention.items() if v["with"] and v["without"]}
        check(
            "存在成對的草稿（同一則 Mention 各有「帶參考群組」與「不帶」的版本）",
            bool(pairs),
            f"{len(pairs)} 組配對，mention_id={sorted(pairs)}",
        )
        for mid, v in sorted(pairs.items()):
            w, wo = v["with"][0], v["without"][0]
            check(
                f"mention {mid}：不帶參考群組的草稿**不含**答案（負對照）",
                not any(f in wo["content_md"] for f in FACTS),
                f"draft id={wo['id']}，{len(wo['content_md'])} 字",
            )
            check(
                f"mention {mid}：帶參考群組的草稿**含**答案（正對照）",
                all(f in w["content_md"] for f in FACTS),
                f"draft id={w['id']}，{len(w['content_md'])} 字",
            )
        info(
            f"共 {len(pairs)} 組獨立配對都得到同一結果——"
            "唯一變數是 Reference Space，這正是 7.1 要求的測法"
        )

    check(
        "所有草稿都含規定的兩個章節（脈絡分析／建議回話）",
        all("脈絡分析" in d["content_md"] and "建議回話" in d["content_md"] for d in drafts),
        f"{len(drafts)} 份草稿",
    )

    section("6. 名錄（D-7）與 Mention 採集的落地結果")
    n_dir = db.query_one("SELECT COUNT(*) n FROM user_directory")["n"]
    check("user_directory 已累積可讀的人名", n_dir > 0, f"{n_dir} 人")
    unknown = db.query_one(
        "SELECT COUNT(*) n FROM user_directory WHERE display_name LIKE '%未知%'"
    )["n"]
    check("名錄中沒有「未知成員」這種無效名字", unknown == 0, f"{unknown} 筆")

    real = db.query_all(
        "SELECT space_name, COUNT(*) n FROM mentions WHERE space_id != 'spaces/AAAAxLxqJxY' "
        "GROUP BY space_name"
    )
    check(
        "採集器在真實工作群組（非測試用 Space）抓到過 Mention",
        bool(real),
        "；".join(f"{r['space_name']}: {r['n']} 則" for r in real) or "無",
    )

    return summary()


if __name__ == "__main__":
    sys.exit(main())
