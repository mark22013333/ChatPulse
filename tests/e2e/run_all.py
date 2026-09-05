"""跑完整套 E2E，彙整結果。

前置條件：
  1. 服務已在 127.0.0.1:8000 執行（scripts/start-web.sh 或直接跑 uvicorn）
  2. 已設定 GOOGLE_API_KEY
  3. 舊 token 沒有 userinfo scope 時，需設 CHATPULSE_BOOTSTRAP_USER_ID

**這套測試會對真實的 Google Chat 與 Gemini 發請求**，並且會在
`spaces/AAAAxLxqJxY`（暫存群組）留下數則測試訊息。**發訊息的目標只有這一個
Space**，e2e_lib.guard_space() 會擋掉任何其他目標。

用法：
    .venv/bin/python tests/e2e/run_all.py
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.environ.get(
    "E2E_REPORT_DIR",
    "/private/tmp/claude-501/-Users-cheng-google-chat-bot/e23e39c0-7bfd-493e-8211-f63e32bb9432/scratchpad",
)

SUITES = [
    # 放最前面：不需要 Gemini 配額，先確立哪些宣稱有落地證據
    ("落地證據（從資料庫重查，零 Gemini 呼叫）", "check_stored_evidence.py"),
    ("AI 供應商切換（實跑走 claude_cli，不動 Gemini 配額）", "test_providers.py"),
    ("Phase 1（認證／Space／訊息／參數／摘要／推播）", "test_phase1.py"),
    ("Phase 2（Mention／Draft Reply／Reference Space／送出／多 Viewer）", "test_phase2.py"),
    ("缺陷 D-3（摘要不被截斷，含 2048 負對照）", "test_d3_truncation.py"),
    ("判定條件 6.1（ADD 不可誤判為 Mention）", "test_add_annotation.py"),
    ("靜態托管與路徑穿越防護", "test_static.py"),
]


def main() -> int:
    # 各套件之間留間隔：Phase 1 與 D-3 都要打 Gemini，連續跑很容易撞到
    # 每分鐘請求上限，結果會表現成「一堆功能壞了」而不是「配額不足」。
    gap = int(os.environ.get("E2E_SUITE_GAP", "20"))

    results = []
    for idx, (label, script) in enumerate(SUITES):
        if idx:
            print(f"\n（間隔 {gap} 秒，避免撞到 Gemini 每分鐘請求上限）")
            time.sleep(gap)
        print(f"\n{'#' * 78}\n# {label}\n#   {script}\n{'#' * 78}")
        report = os.path.join(SCRATCH, script.replace(".py", ".md"))
        env = {**os.environ, "E2E_REPORT": report}
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, script)],
            env=env,
            capture_output=True,
            text=True,
        )
        elapsed = time.time() - t0
        out = proc.stdout
        # 從各套件的結尾統計行取數字
        tally = ""
        for line in out.splitlines():
            if line.startswith("合計"):
                tally = line
        print(out[-1500:] if len(out) > 1500 else out)
        if proc.returncode != 0 and proc.stderr:
            print("--- stderr ---")
            print(proc.stderr[-1200:])
        results.append((label, script, proc.returncode, tally, elapsed))

    print(f"\n{'=' * 78}\n總結\n{'=' * 78}")
    failed = 0
    blocked_suites = 0
    for label, script, rc, tally, elapsed in results:
        icon = {0: "✅", 2: "⏸️"}.get(rc, "❌")
        if rc == 1:
            failed += 1
        elif rc == 2:
            blocked_suites += 1
        print(f"{icon} {label}")
        print(f"     {script}｜{tally or '（無統計行）'}｜{elapsed:.0f}s")
    print(f"\n{len(results)} 套測試，失敗 {failed} 套，含無法驗證項目 {blocked_suites} 套")
    print(f"逐套明細報告：{SCRATCH}/test_*.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
