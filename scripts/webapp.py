"""Web 儀表板的啟動流程（跨平台，單一事實來源）。

**為什麼這個檔案存在**：這段邏輯原本有兩份——`scripts/start-web.sh` 給
macOS/Linux，`scripts/onboard.py` 裡另寫一行 uvicorn 給 Windows。結果那行
少了前端建置，Windows 使用者一啟動就撞上「找不到前端建置產物」的白底黑字，
而維護者在 macOS 上怎麼試都是好的。這正是專案開頭就警告過的漂移，卻仍然
發生在同一個檔案的 if/else 兩側。

所以現在只有一份：`.sh` 與 `.bat` 都是薄殼，兩個平台跑的是這裡的同一段程式碼。

**前端建置產物的策略**（ADR 見 SETUP_GUIDE「前端建置產物」）：
`dashboard/frontend/dist/` 進版控，所以 clone 下來就有畫面，不需要 Node.js。
只有「原始碼比產物新」時才需要重建，而那只會發生在改前端的維護者身上。
新舊判斷用內容雜湊而不是 mtime——clone 之後所有檔案的 mtime 都是 checkout
當下的時間，用 mtime 會讓每個同事一 clone 就被要求重建。
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import webbrowser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IS_WINDOWS = platform.system() == "Windows"

VENV_DIR = os.path.join(BASE_DIR, ".venv")
VENV_PYTHON = os.path.join(
    VENV_DIR, "Scripts" if IS_WINDOWS else "bin", "python.exe" if IS_WINDOWS else "python"
)

FRONTEND_DIR = os.path.join(BASE_DIR, "dashboard", "frontend")
DIST_DIR = os.path.join(FRONTEND_DIR, "dist")
DIST_INDEX = os.path.join(DIST_DIR, "index.html")
BUILDINFO = os.path.join(DIST_DIR, ".buildinfo.json")

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://localhost:{PORT}"


def unbuffer_output() -> None:
    """讓自己的輸出即時送出，不要被緩衝到子行程輸出的後面。

    Python 在 stdout 不是終端機時（被導向檔案或 pipe）會整批緩衝，但子行程
    （pip、npm、uvicorn）是直接寫檔案描述子的。結果是引導訊息全部堆到最後，
    排在子行程輸出之後——「正在建置…」出現在建置完成的訊息下面，順序一亂
    就完全看不懂。同事把畫面存成檔案回報問題時，看到的就是這種東西。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, OSError, ValueError):
            pass

# 只有這些東西會影響建置產物。node_modules 與 dist 本身不算，
# 測試檔（*.test.ts / *.test.tsx）也不算——改測試不需要重建畫面。
_SOURCE_FILES = ("index.html", "package.json", "vite.config.ts", "tsconfig.json",
                 "components.json")
_SOURCE_DIRS = ("src",)
_IGNORED_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")


# ----------------------------------------------------------------------
# 建置產物的新舊判斷
# ----------------------------------------------------------------------

def _iter_source_files():
    """列出所有會影響建置產物的檔案，路徑相對於 FRONTEND_DIR。"""
    for name in _SOURCE_FILES:
        path = os.path.join(FRONTEND_DIR, name)
        if os.path.isfile(path):
            yield name, path
    for dirname in _SOURCE_DIRS:
        root_dir = os.path.join(FRONTEND_DIR, dirname)
        for root, dirs, files in os.walk(root_dir):
            dirs.sort()
            for fname in sorted(files):
                if fname.endswith(_IGNORED_SUFFIXES):
                    continue
                path = os.path.join(root, fname)
                rel = os.path.relpath(path, FRONTEND_DIR).replace(os.sep, "/")
                yield rel, path


def source_hash() -> str:
    """前端原始碼的內容指紋。

    用內容而非 mtime：git clone / 解壓縮都會重寫 mtime，但不會改內容。
    路徑一起計入，這樣新增或刪除檔案也算變動。

    兩個為了跨平台一致而做的正規化，少一個都會讓 Windows 使用者一 clone
    就被誤判成「原始碼比產物新」（指紋是在 macOS 上算好寫進版控的）：

      1. 路徑分隔符統一成 `/`——Windows 的 os.sep 是反斜線。
      2. 行尾統一成 LF——git 的 `core.autocrlf` 在 Windows 預設會把文字檔
         checkout 成 CRLF，內容一個位元組都沒被人改過，位元組序列卻不同。
    """
    digest = hashlib.sha256()
    for rel, path in _iter_source_files():
        digest.update(rel.encode("utf-8"))
        try:
            with open(path, "rb") as fh:
                # 對二進位檔（字型、圖片）做這個取代理論上會讓不同內容碰撞，
                # 但這裡只需要偵測「有沒有變動」，不是防篡改。
                digest.update(fh.read().replace(b"\r\n", b"\n"))
        except OSError:
            # 讀不到就把這件事本身算進指紋，下次讀得到時指紋會變
            digest.update(b"<unreadable>")
    return digest.hexdigest()


def _read_buildinfo() -> dict:
    try:
        with open(BUILDINFO, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_buildinfo(digest: str) -> None:
    import datetime

    try:
        with open(BUILDINFO, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "source_hash": digest,
                    "built_at": datetime.datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "note": "由 scripts/webapp.py 產生，用來判斷 dist 是否需要重建。",
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
            fh.write("\n")
    except OSError:
        pass  # 蓋不了章只是下次會多建一次，不該讓啟動失敗


def frontend_state() -> str:
    """回傳 'ready'（可直接用）、'stale'（原始碼較新）或 'missing'（沒有產物）。"""
    if not os.path.isfile(DIST_INDEX):
        return "missing"
    recorded = _read_buildinfo().get("source_hash")
    if not recorded:
        # 有產物但沒蓋章：可能是別人手動 npm run build 的。
        # 無從判斷新舊，當作可用——誤判成 stale 會沒事找事重建。
        return "ready"
    return "ready" if recorded == source_hash() else "stale"


# ----------------------------------------------------------------------
# 建置
# ----------------------------------------------------------------------

def find_npm() -> str:
    """npm 的完整路徑，找不到回空字串。

    Windows 上 npm 實際是 `npm.cmd`，`subprocess` 不會自己套用 PATHEXT，
    直接傳 "npm" 會 FileNotFoundError。`shutil.which` 會處理這件事，
    所以一律用它拿到完整路徑再執行。
    """
    return shutil.which("npm") or ""


def build_frontend(npm: str, log=print) -> bool:
    """建置前端。回傳是否成功。"""
    if not os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules")):
        log("  正在安裝前端套件（第一次需要幾分鐘，之後就不用了）…")
        if subprocess.call([npm, "install"], cwd=FRONTEND_DIR) != 0:
            return False
    log("  正在建置畫面…")
    if subprocess.call([npm, "run", "build"], cwd=FRONTEND_DIR) != 0:
        return False
    if not os.path.isfile(DIST_INDEX):
        return False
    _write_buildinfo(source_hash())
    return True


# ----------------------------------------------------------------------
# 供應商檢查
# ----------------------------------------------------------------------

_PROVIDER_PROBE = (
    "import sys; sys.path.insert(0, r'{base}')\n"
    "from core import providers\n"
    "avail = [d for d in providers.describe_all() if d['available']]\n"
    "if avail:\n"
    "    print('OK|' + '、'.join(d['name'] for d in avail) + '|' + providers.default_name())\n"
    "else:\n"
    "    print('NONE|' + '；'.join(f\"{{d['name']}}：{{d['reason']}}\" for d in providers.describe_all()))\n"
)


def provider_summary(python: str = "") -> tuple:
    """回傳 (狀態, 說明)。狀態為 'ok' / 'none' / 'unknown'。"""
    python = python or (VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable)
    try:
        out = subprocess.run(
            [python, "-c", _PROVIDER_PROBE.format(base=BASE_DIR)],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", ""
    line = (out.stdout or "").strip().splitlines()
    if not line:
        return "unknown", ""
    head, _, rest = line[-1].partition("|")
    if head == "OK":
        names, _, default = rest.partition("|")
        return "ok", f"{names}（預設 {default}）"
    if head == "NONE":
        return "none", rest
    return "unknown", ""


# ----------------------------------------------------------------------
# 啟動
# ----------------------------------------------------------------------

def _open_browser_later(delay: float = 2.0) -> None:
    """背景延遲開瀏覽器，等 uvicorn 綁好 port。

    webbrowser 模組跨平台（Windows 走 os.startfile、macOS 走 open），
    所以不需要為各平台各寫一份。
    """
    def _go():
        try:
            webbrowser.open(URL)
        except Exception:
            pass  # 開不起來不影響服務，使用者自己貼網址就好

    timer = threading.Timer(delay, _go)
    timer.daemon = True
    timer.start()


def start(ui, dev: bool = False) -> int:
    """啟動儀表板。

    `ui` 是輸出工具的集合（onboard.py 的 ok/warn/bad/explain/title），
    這樣訊息風格與安裝引導一致，不會一半彩色一半不是。
    """
    ui.title("啟動 Web 儀表板")

    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
    if not os.path.exists(VENV_PYTHON):
        ui.warn("找不到專案環境，改用目前的 Python（建議先跑一次完整引導）")

    # 依賴檢查要擋在最前面。少了 uvicorn 而讓流程繼續，使用者會先看到我們
    # 一路報「畫面已備妥」，最後才吃到一行 Python 的 ModuleNotFoundError。
    probe = subprocess.run(
        [python, "-c", "import fastapi, uvicorn"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        ui.bad("必要套件不完整，無法啟動",
               f'執行："{python}" -m pip install -r requirements.txt')
        ui.explain(f"原始錯誤：{(probe.stderr or '').strip()[:200]}")
        return 1

    # ── 1. 畫面 ──────────────────────────────────────────────
    state = frontend_state()
    have_ui = state != "missing"
    npm = find_npm()

    if state == "ready":
        ui.ok("畫面已備妥")
    elif state == "stale":
        if npm:
            ui.warn("前端原始碼比建置產物新，正在重建")
            if build_frontend(npm, log=ui.plain):
                ui.ok("重建完成")
                ui.explain(
                    """
                    dashboard/frontend/dist/ 有版控，這次重建的結果請記得一起 commit，
                    否則其他人 clone 下來看到的還是舊畫面。
                    """
                )
            else:
                ui.warn("重建失敗，先用現有的舊畫面啟動")
        else:
            ui.warn("前端原始碼比建置產物新，但這台沒有 npm——先用現有畫面啟動")
    else:  # missing
        if npm:
            ui.warn("還沒有畫面的建置產物，現在建置")
            have_ui = build_frontend(npm, log=ui.plain)
            if have_ui:
                ui.ok("建置完成")
            else:
                ui.bad("前端建置失敗")
        else:
            ui.bad("無法準備儀表板畫面")
            print()
            ui.arrow("這台電腦沒有 Node.js，而版控裡也找不到建置產物")
            ui.arrow("最省事：改用 Claude Code 入口，那個不需要 Node.js")
            launcher = "chatpulse.bat" if IS_WINDOWS else "./chatpulse.sh"
            ui.plain(f"       {launcher}   然後選第 1 項")
            ui.arrow("或安裝 Node.js 後重跑：https://nodejs.org/")

    # ── 2. AI 供應商 ─────────────────────────────────────────
    status, detail = provider_summary(python)
    if status == "ok":
        ui.ok(f"AI 供應商可用：{detail}")
    elif status == "none":
        ui.warn("沒有可用的 AI 供應商——群組與訊息讀得到，但摘要與回話草稿會失敗")
        ui.explain(detail.replace("；", "\n"))
        ui.explain("最省事的解法：裝好並登入 Claude Code，它用你現有的訂閱，不需要 API key。")

    # ── 3. 啟動 ─────────────────────────────────────────────
    print()
    if have_ui:
        ui.ok(f"儀表板：{URL}")
        ui.plain("       瀏覽器會自動開啟；要停止服務請在這個視窗按 Ctrl+C")
        _open_browser_later()
    else:
        # 沒畫面就不開瀏覽器——把人丟到一個看不懂的錯誤頁，
        # 只會讓他離「該怎麼辦」更遠。指引留在這個視窗裡。
        ui.warn(f"API 仍在 {HOST}:{PORT} 執行中（Ctrl+C 停止），但沒有畫面可看")

    print()
    args = [python, "-m", "uvicorn", "dashboard.api.server:app",
            "--host", HOST, "--port", str(PORT)]
    if dev:
        args += ["--reload", "--reload-dir", os.path.join(BASE_DIR, "dashboard"),
                 "--reload-dir", os.path.join(BASE_DIR, "core")]
    try:
        return subprocess.call(args, cwd=BASE_DIR)
    except KeyboardInterrupt:
        return 0
    except FileNotFoundError:
        ui.bad("啟動失敗：找不到 uvicorn",
               f'執行："{python}" -m pip install -r requirements.txt')
        return 1
