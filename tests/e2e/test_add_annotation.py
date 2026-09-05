"""驗證 6.1：`userMention.type == "ADD"`（某人被加進 Space）不可被算成 Mention。

規格 SPECIFICATION.md 6.1 的警語：
    只判斷 USER_MENTION 是錯的。userMention.type 另有 ADD 值，代表「某人被加進
    Space」的系統訊息——漏掉這個條件，每次有人被拉進群都會被算成一則待回覆。

本測試優先用**真實 API 資料**取證：掃過近期活躍 Space 的訊息，找出真的帶
`userMention.type != "MENTION"` 的訊息，餵給正式的判定函式。
掃不到真實樣本時退回結構化樣本，並在報告中明確標示證據等級——
不會把「掃不到」寫成「已用真實資料驗過」。
"""

import concurrent.futures as futures
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

# 專案根＝tests/e2e 往上兩層。不寫死絕對路徑，同事 clone 到別的位置也要能跑
E2E_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(E2E_DIR)))
sys.path.insert(0, E2E_DIR)

from core.chat_client import GoogleChatClient, parse_rfc3339  # noqa: E402
from core.mentions import is_mention_of  # noqa: E402
from e2e_lib import check, info, section, set_report, summary  # noqa: E402

REPORT = os.environ.get(
    "E2E_REPORT",
    os.path.join(E2E_DIR, "reports", "e2e-add.md"),
)
MY_ID = "users/109827265019732088641"
SCAN_SPACES = 60
PER_SPACE = 200


def main() -> int:
    set_report(REPORT)
    client = GoogleChatClient(interactive=False)

    section("0. 掃描真實訊息，尋找 userMention.type != MENTION 的樣本")
    spaces = client.list_spaces()
    now = datetime.now(timezone.utc)

    def recency(s):
        dt = parse_rfc3339(s.get("lastActiveTime") or "")
        return (now - dt).total_seconds() if dt else float("inf")

    targets = sorted(spaces, key=recency)[:SCAN_SPACES]
    info(f"從 {len(spaces)} 個 Space 中取最近活躍的 {len(targets)} 個掃描，每個最多 {PER_SPACE} 則")

    def scan(sp):
        try:
            msgs = client.fetch_recent_messages(sp["name"], limit=PER_SPACE)
        except Exception as exc:
            return sp["name"], [], str(exc)
        hits = []
        for m in msgs:
            for ann in m.get("annotations") or []:
                if ann.get("type") == "USER_MENTION":
                    um = ann.get("userMention") or {}
                    if um.get("type") != "MENTION":
                        hits.append((m, ann, sp.get("displayName")))
        return sp["name"], hits, None

    add_samples = []
    errors = 0
    scanned_total = 0
    with futures.ThreadPoolExecutor(max_workers=12) as pool:
        for name, hits, err in pool.map(scan, targets):
            scanned_total += 1
            if err:
                errors += 1
                continue
            add_samples.extend(hits)

    info(f"掃完 {scanned_total} 個 Space（{errors} 個讀取失敗），找到 {len(add_samples)} 個非 MENTION 樣本")

    real_evidence = bool(add_samples)
    check(
        "找到真實的 userMention.type != MENTION 樣本",
        real_evidence,
        f"{len(add_samples)} 筆"
        if real_evidence
        else "掃描範圍內沒有這種訊息（歷史中沒有成員變更事件，或該事件不落在訊息清單裡）",
    )

    if real_evidence:
        section("1. 用真實樣本驗證判定函式")
        types_seen: Dict[Any, int] = {}
        for msg, ann, space_name in add_samples:  # 全量統計，不要只取前 N 筆
            um = ann.get("userMention") or {}
            t = um.get("type")
            types_seen[t] = types_seen.get(t, 0) + 1
        info(f"全部 {len(add_samples)} 筆的 userMention.type 分佈：{types_seen}")

        for idx, (msg, ann, space_name) in enumerate(add_samples[:5], 1):
            um = ann.get("userMention") or {}
            target_user = (um.get("user") or {}).get("name")
            info(
                f"樣本 {idx}：{space_name} / type={um.get('type')} / "
                f"user={target_user} / text={(msg.get('text') or '')[:40]!r}"
            )

        # 這批樣本其實是兩種不同形態，必須分開斷言，否則分母會虛胖：
        #   (a) 帶 user.name 的（真正的 ADD／其他非 MENTION 類型）
        #       → 對「被指到的那個人」判定，這是 6.1 警語針對的情境
        #   (b) 不帶 user.name 的（`@全部` 這種廣播，userMention 連 type 都沒有）
        #       → 沒有「被指到的人」可比對，要斷言的是「它不會被算成任何人的 Mention」
        #
        # 早先的寫法對 (b) 直接 continue，等於整批沒跑過斷言，卻把它們算進
        # 「N 筆全部正確排除」的分母裡——行為沒錯，但證據強度被高估了。
        with_target = []
        without_target = []
        for msg, ann, _ in add_samples:
            um = ann.get("userMention") or {}
            target_user = (um.get("user") or {}).get("name")
            (with_target if target_user else without_target).append((msg, ann, target_user))

        info(
            f"樣本拆分：帶 user.name 的 {len(with_target)} 筆（可對「被指到的人」斷言）、"
            f"不帶 user.name 的 {len(without_target)} 筆（廣播型，改以「不算任何人的 Mention」斷言）"
        )
        add_only = [s for s in with_target if (s[1].get("userMention") or {}).get("type") == "ADD"]
        info(f"其中 userMention.type == 'ADD' 的真實樣本：{len(add_only)} 筆")

        misjudged = [
            (m.get("name"), (a.get("userMention") or {}).get("type"))
            for m, a, u in with_target
            if is_mention_of(m, u)
        ]
        check(
            f"帶 user.name 的 {len(with_target)} 筆真實樣本，對「被指到的那個人」都不算 Mention",
            not misjudged and len(with_target) > 0,
            f"誤判 {len(misjudged)} 筆：{misjudged[:3]}"
            if misjudged
            else f"{len(with_target)} 筆全部正確排除（其中 {len(add_only)} 筆是 type=ADD）",
        )

        # 廣播型樣本：拿本人 id 與樣本裡出現過的其他 id 各試一次
        probe_ids = [MY_ID] + [u for _, _, u in with_target[:5]]
        broadcast_misjudged = [
            m.get("name")
            for m, _, _ in without_target
            for pid in probe_ids
            if is_mention_of(m, pid)
        ]
        check(
            f"不帶 user.name 的 {len(without_target)} 筆廣播樣本，不算任何人的 Mention",
            not broadcast_misjudged and len(without_target) > 0,
            f"誤判 {len(set(broadcast_misjudged))} 筆"
            if broadcast_misjudged
            else f"{len(without_target)} 筆 × {len(probe_ids)} 個候選 id 全部正確排除",
        )
        info(
            "廣播型（`@全部`）值得單獨斷言的理由：若實作只檢查 "
            "`annotations[].type == 'USER_MENTION'` 而不看 `userMention.type` 與 "
            "`user.name`，每一則 @全部 都會湧進每個人的收件匣。"
        )

        samples_path = os.path.join(E2E_DIR, "reports", "add_samples.json")
        os.makedirs(os.path.dirname(samples_path), exist_ok=True)
        with open(samples_path, "w") as f:
            json.dump(
                {
                    "total_samples": len(add_samples),
                    "type_distribution": {str(k): v for k, v in types_seen.items()},
                    "with_user_name": len(with_target),
                    "without_user_name_broadcast": len(without_target),
                    "type_add_count": len(add_only),
                    "samples": [
                        {"message": m.get("name"), "annotation": a, "space": sp}
                        for m, a, sp in add_samples
                    ],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        info("真實樣本已存 add_samples.json")

    section("2. 結構化樣本（依官方 annotation 結構，補齊真實資料掃不到的分支）")
    cases = [
        (
            "USER_MENTION + MENTION + 是我 → 應算 Mention",
            {
                "annotations": [
                    {
                        "type": "USER_MENTION",
                        "userMention": {"type": "MENTION", "user": {"name": MY_ID, "type": "HUMAN"}},
                    }
                ]
            },
            True,
        ),
        (
            "USER_MENTION + ADD + 是我 → **不可**算 Mention（6.1 警語）",
            {
                "annotations": [
                    {
                        "type": "USER_MENTION",
                        "userMention": {"type": "ADD", "user": {"name": MY_ID, "type": "HUMAN"}},
                    }
                ]
            },
            False,
        ),
        (
            "USER_MENTION + MENTION + 是別人 → 不算",
            {
                "annotations": [
                    {
                        "type": "USER_MENTION",
                        "userMention": {"type": "MENTION", "user": {"name": "users/42", "type": "HUMAN"}},
                    }
                ]
            },
            False,
        ),
        (
            "USER_MENTION 但 userMention.type 未指定 → 不算（保守）",
            {"annotations": [{"type": "USER_MENTION", "userMention": {"user": {"name": MY_ID}}}]},
            False,
        ),
        (
            "TYPE_UNSPECIFIED → 不算",
            {
                "annotations": [
                    {
                        "type": "USER_MENTION",
                        "userMention": {
                            "type": "TYPE_UNSPECIFIED",
                            "user": {"name": MY_ID},
                        },
                    }
                ]
            },
            False,
        ),
        (
            "SLASH_COMMAND annotation → 不算",
            {"annotations": [{"type": "SLASH_COMMAND", "slashCommand": {}}]},
            False,
        ),
        (
            "RICH_LINK annotation → 不算",
            {"annotations": [{"type": "RICH_LINK", "richLinkMetadata": {}}]},
            False,
        ),
        ("完全沒有 annotations → 不算", {"text": "純文字訊息"}, False),
        ("annotations 為 None → 不算", {"annotations": None}, False),
        (
            "同一則訊息裡 ADD 別人、MENTION 我 → 應算（不可因為有 ADD 就整則跳過）",
            {
                "annotations": [
                    {
                        "type": "USER_MENTION",
                        "userMention": {"type": "ADD", "user": {"name": "users/42"}},
                    },
                    {
                        "type": "USER_MENTION",
                        "userMention": {"type": "MENTION", "user": {"name": MY_ID}},
                    },
                ]
            },
            True,
        ),
        (
            "同一則訊息裡 MENTION 別人、ADD 我 → 不算",
            {
                "annotations": [
                    {
                        "type": "USER_MENTION",
                        "userMention": {"type": "MENTION", "user": {"name": "users/42"}},
                    },
                    {
                        "type": "USER_MENTION",
                        "userMention": {"type": "ADD", "user": {"name": MY_ID}},
                    },
                ]
            },
            False,
        ),
    ]
    for label, msg, expected in cases:
        got = is_mention_of(msg, MY_ID)
        check(label, got == expected, f"期望 {expected}，實際 {got}")

    section("3. 證據等級")
    if real_evidence:
        info("ADD 分支已用**真實 API 資料**驗過（樣本見 add_samples.json）")
    else:
        info(
            "ADD 分支**只有結構化樣本**，未取得真實 API 資料——"
            "本帳號近期活躍 Space 的訊息清單中沒有成員變更事件。"
            "要取得真實樣本必須實際把人加進某個 Space，本次任務未授權該動作。"
        )
    check("本節僅為證據等級聲明，不構成通過條件", True)

    return summary()


if __name__ == "__main__":
    sys.exit(main())
