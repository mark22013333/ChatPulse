"""跑完所有瀏覽器 E2E（`.cjs`），彙整結果。

`run_all.py` 只跑 Python 套件（它用 `sys.executable` 起子程序），五支
Playwright 驅動的 `.cjs` 一直沒有群組入口、只能一支一支 `node` 手動跑。
這個檔補上那個入口。

前置條件：
  1. 服務已在 127.0.0.1:8000 執行（`./chatpulse.sh web`）
  2. 全域安裝 playwright（`npm i -g playwright`）
  3. 資料庫裡有授權過的 Viewer

**認證：強烈建議先設 `CHATPULSE_SESSION`**，那條路重用一個既有的 session、
**零資料庫寫入**。沒設的話各套件會去點畫面上的「匯入既有憑證」，那會在
sessions 表 INSERT 一筆。取得方式（唯讀查詢）：

    export CHATPULSE_SESSION=$(.venv/bin/python -c "
    import sqlite3, sys; sys.path.insert(0, '.')
    from core import config as cfg
    con = sqlite3.connect(cfg.DB_PATH)
    row = con.execute(
        \\"select token from sessions where expires_at > datetime('now') \\"
        'order by created_at desc limit 1').fetchone()
    print(row[0] if row else '')")

注意 `test_merge_reply` 與 `test_draft_from_summary` 需要資料庫裡有特定形狀
的資料（同一個 Space 至少兩則待處理／有摘要可以按「產生回覆草稿」），
沒有的話它們會如實回報找不到，而不是假裝通過。

用法：
    .venv/bin/python tests/e2e/run_browser.py
    .venv/bin/python tests/e2e/run_browser.py test_ui_redesign   # 只跑一支
"""

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    # 放最前面：不需要特定資料形狀，任何一個有 Viewer 的資料庫都跑得完
    ("介面改版：路由／設定覆蓋層／命令面板／鍵盤導航", "test_ui_redesign.cjs"),
    ("訊息預覽", "test_message_preview.cjs"),
    ("切頁籤不中止串流", "test_tab_switch_streaming.cjs"),
    ("收件匣多選、合併成一則回話", "test_merge_reply.cjs"),
    ("從摘要工作台產生草稿", "test_draft_from_summary.cjs"),
]


def main() -> int:
    if not shutil.which("node"):
        print("找不到 node。瀏覽器 E2E 需要 node ＋ 全域安裝的 playwright。")
        return 2

    only = sys.argv[1] if len(sys.argv) > 1 else None
    suites = [s for s in SUITES if not only or s[1].startswith(only)]
    if not suites:
        print(f"沒有符合「{only}」的套件。可用：")
        for _, script in SUITES:
            print(f"  {script}")
        return 2

    if not os.environ.get("CHATPULSE_SESSION"):
        print(
            "⚠ 沒有 CHATPULSE_SESSION：各套件會去點「匯入既有憑證」，"
            "那會對正式資料庫 INSERT 一筆 session。\n"
            "  設定方式見本檔開頭的說明（唯讀取得既有 token，零寫入）。\n"
        )

    results = []
    for idx, (label, script) in enumerate(suites):
        if idx:
            # 各套件之間留一點間隔：它們都會打真實後端，連續跑容易撞到
            # Space 清單的快取重整
            time.sleep(3)
        print(f"\n{'#' * 78}\n# {label}\n#   {script}\n{'#' * 78}")
        t0 = time.time()
        proc = subprocess.run(
            ["node", os.path.join(HERE, script)],
            capture_output=True,
            text=True,
        )
        elapsed = time.time() - t0
        print(proc.stdout[-2500:] if len(proc.stdout) > 2500 else proc.stdout)
        if proc.returncode != 0 and proc.stderr:
            print("--- stderr ---")
            print(proc.stderr[-1200:])
        # 從各套件結尾的「N/M 項通過」取數字
        tally = ""
        for line in proc.stdout.splitlines():
            if "項通過" in line:
                tally = line.strip()
        results.append((label, script, proc.returncode, tally, elapsed))

    print(f"\n{'=' * 78}\n總結\n{'=' * 78}")
    failed = 0
    crashed = 0
    for label, script, rc, tally, elapsed in results:
        icon = {0: "✅", 2: "💥"}.get(rc, "❌")
        if rc == 1:
            failed += 1
        elif rc != 0:
            crashed += 1
        print(f"{icon} {label}")
        print(f"     {script}｜{tally or '（無統計行）'}｜{elapsed:.0f}s")
    print(f"\n{len(results)} 套測試，斷言失敗 {failed} 套，測試本身炸掉 {crashed} 套")
    return 1 if (failed or crashed) else 0


if __name__ == "__main__":
    sys.exit(main())
