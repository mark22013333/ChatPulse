"""ZPlanner client 的冒煙驗證：對**正式站**實際打一輪唯讀端點。

**這支腳本不在 CI 跑，也不該被自動化排程呼叫。** 它需要一把真的
ZPLANNER_APIKEY，而 CI 不會有、也不該有。用途是：換 token、換網域、或改動
`core/zplanner_client.py` 之後，用一次真實往返確認「我們對這支 API 的理解
仍然成立」——單元測試守的是邏輯，這支守的是對外部系統的假設。

**全部是 GET，不寫入任何資料。** 唯一會變動的東西是 ZPlanner 那側的 access
log。要加新端點進來之前，先確認它也是唯讀。

用法：

    .venv/bin/python scripts/zplanner_smoke.py

輸出不含 token。專案與 issue 的標題只印前幾筆、且截斷，避免把整份工作內容
倒進終端 scrollback。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.errors import ChatPulseError  # noqa: E402
from core.zplanner_client import ZPlannerClient  # noqa: E402

PASS = "  ✅"
FAIL = "  ❌"
SKIP = "  ⏭️"


#: 認得的識別欄位。**role 與 user_name 不可拿掉**：role-permissions 與
#: members 兩支端點沒有 name/title，少了這兩個欄位就只印得出流水號 id，
#: 看起來像「有回資料但沒有內容」。
_BRIEF_KEYS = (
    "id",
    "issue_key",
    "key",
    "code",
    "role",
    "user_name",
    "name",
    "title",
    "subject",
)


def brief(row, keys=_BRIEF_KEYS):
    """把一筆資料縮成一行，只取認得的欄位並截斷。

    認不出任何欄位時印出**完整**的 key 清單而不是前幾個——截斷過的清單會讓
    「這個欄位不存在」看起來成立，實際上只是排在後面（2026-09-13 就是這樣
    差點誤判 can_worklog 不存在）。
    """
    if not isinstance(row, dict):
        return repr(row)[:80]
    parts = []
    for k in keys:
        if k in row and row[k] not in (None, ""):
            parts.append(f"{k}={str(row[k])[:40]}")
    return "  ".join(parts) or f"（欄位：{sorted(row)}）"


def run(label, fn):
    """跑一個端點，回傳 (成功?, 結果)。錯誤不中斷後續檢查。"""
    print(f"\n▶ {label}")
    try:
        result = fn()
    except ChatPulseError as exc:
        # 這裡印得出 code 就代表錯誤轉譯有作用——沒有被當成成功吞掉
        print(f"{FAIL} {type(exc).__name__}（{exc.code}）：{exc.message}")
        if exc.detail:
            print(f"     ZPlanner 原文：{exc.detail[:120]}")
        return False, None
    except Exception as exc:  # noqa: BLE001 — 冒煙腳本要看得到任何非預期例外
        print(f"{FAIL} 非預期的例外 {type(exc).__name__}：{exc}")
        return False, None

    if isinstance(result, list):
        print(f"{PASS} 回傳 {len(result)} 筆")
        for row in result[:3]:
            print(f"     {brief(row)}")
        if len(result) > 3:
            print(f"     …（其餘 {len(result) - 3} 筆略）")
    else:
        print(f"{PASS} 回傳 {type(result).__name__}")
        if isinstance(result, dict):
            print(f"     欄位：{sorted(result)[:10]}")
    return True, result


def main():
    client = ZPlannerClient()

    ok, reason = client.available()
    print(f"設定檢查：{reason}")
    if not ok:
        print("\n無法繼續：先設定 ZPLANNER_APIKEY。")
        return 1

    results = {}

    ok, projects = run("GET /api/projects/（專案列表）", client.list_projects)
    results["projects"] = ok

    ok, mine = run("GET /api/issues/mine/（指派給我的 issue）", client.list_my_issues)
    results["mine"] = ok

    # worklogable 的 q 必填。關鍵字優先從 mine 的 issue_key 取前綴（例如 PEI-12
    # 取 PEI），那是「已知必然有資料」的正對照；取不到才退回專案名稱。
    keyword = None
    if mine:
        for row in mine:
            key = row.get("issue_key") or row.get("key") or ""
            if isinstance(key, str) and "-" in key:
                keyword = key.split("-")[0]
                break
    if not keyword and projects:
        first = projects[0]
        keyword = str(first.get("code") or first.get("name") or "")[:6] or None

    if keyword:
        ok, worklogable = run(
            f"GET /api/issues/worklogable/?q={keyword}（可填工時的 issue）",
            lambda: client.list_worklogable_issues(keyword),
        )
        results["worklogable"] = ok
        if ok and mine is not None and worklogable is not None:
            # 筆記實測：兩者是不同集合，worklogable 甚至可以比 mine 多
            print(
                f"     對照：mine {len(mine)} 筆 vs worklogable(q={keyword}) "
                f"{len(worklogable)} 筆——兩者本來就不該相等"
            )
    else:
        print(f"\n▶ GET /api/issues/worklogable/\n{SKIP} 找不到可用的關鍵字，跳過")
        results["worklogable"] = None

    # q 必填這件事的正對照：空字串必須被 client 擋下，不該送出去
    print("\n▶ worklogable 空關鍵字（應被 client 擋下，不送出請求）")
    try:
        client.list_worklogable_issues("")
        print(f"{FAIL} 沒有擋下來——q 必填的防呆失效了")
        results["worklogable_guard"] = False
    except ChatPulseError as exc:
        print(f"{PASS} 已擋下：{exc.code}")
        results["worklogable_guard"] = True

    # 專案層端點需要一個 project id
    project_id = None
    if projects:
        project_id = projects[0].get("id")
    if project_id is None:
        print(f"\n{SKIP} 沒有可用的 project id，跳過三個專案層端點")
    else:
        name = str(projects[0].get("name", ""))[:30]
        print(f"\n（以下三支用 project_id={project_id}「{name}」測試）")
        results["project_issues"] = run(
            f"GET /api/projects/{project_id}/issues/（會自動翻頁）",
            lambda: client.list_project_issues(project_id),
        )[0]
        results["role_permissions"] = run(
            f"GET /api/projects/{project_id}/role-permissions/",
            lambda: client.list_project_role_permissions(project_id),
        )[0]
        results["members"] = run(
            f"GET /api/projects/{project_id}/members/",
            lambda: client.list_project_members(project_id),
        )[0]

    print("\n" + "=" * 60)
    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    print(f"通過 {passed}　失敗 {failed}　跳過 {skipped}")
    for k, v in results.items():
        mark = {True: "通過", False: "失敗", None: "跳過"}[v]
        print(f"  {k}: {mark}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
