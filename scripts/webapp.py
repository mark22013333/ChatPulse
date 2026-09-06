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

import errno
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
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

    順便把編碼錯誤改成不中止：這些訊息裡有中文與 ✓✗ 符號，而 Windows 上
    如果沒經過 chatpulse.bat（它會設 chcp 65001 與 PYTHONUTF8），主控台預設是
    cp950，印這些字元會直接拋 UnicodeEncodeError 讓引導中止。顯示成 ? 很難看，
    但比整個流程停在一個看不懂的例外好。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, errors="replace")
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

# 專案路徑用 argv 傳進去，不要內嵌進原始碼字串。內嵌的話路徑含單引號
# （`C:\Users\O'Brien\…` 這種姓氏並不罕見）會讓探針語法錯誤，而錯誤被
# capture_output 吃掉，結果是「AI 供應商」那一項安靜地什麼都不顯示。
_PROVIDER_PROBE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from core import providers\n"
    "avail = [d for d in providers.describe_all() if d['available']]\n"
    "if avail:\n"
    "    print('OK|' + '、'.join(d['name'] for d in avail) + '|' + providers.default_name())\n"
    "else:\n"
    "    print('NONE|' + '；'.join(f\"{d['name']}：{d['reason']}\" for d in providers.describe_all()))\n"
)


def provider_summary(python: str = "") -> tuple:
    """回傳 (狀態, 說明)。狀態為 'ok' / 'none' / 'unknown'。"""
    python = python or (VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable)
    try:
        out = subprocess.run(
            [python, "-c", _PROVIDER_PROBE, BASE_DIR],
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

def port_in_use(port: int = PORT) -> bool:
    """有沒有人在這個 port 上聽（用來判斷服務起來了沒）。"""
    try:
        with socket.create_connection((HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def port_available(port: int = PORT) -> tuple:
    """能不能綁這個 port。回傳 (可用, 原因)，原因為 '' / 'in_use' / 'denied'。

    用 bind 而不是 connect：connect 只問「有沒有人在聽」，但真正決定服務
    起不起得來的是「綁不綁得上」，兩者不等價。Windows 上 Hyper-V 與 WSL2
    會保留成段的動態連接埠，那些 port **沒有人在 listen**（connect 測不出來）
    卻會讓 bind 被拒（WSAEACCES）——只用 connect 判斷就會放行，
    然後使用者拿到一行英文的 winerror 10013。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 刻意不設 SO_REUSEADDR：這裡要模擬的就是 uvicorn 待會的處境
        sock.bind((HOST, port))
        return True, ""
    except OSError as exc:
        in_use_codes = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)}
        return False, ("in_use" if exc.errno in in_use_codes else "denied")
    finally:
        sock.close()


def _open_browser_when_ready(proc, timeout: float = 25.0) -> None:
    """等服務真的起來之後才開瀏覽器。

    不用固定延遲：啟動時間受機器速度與資料庫大小影響，猜短了會開出
    「無法連線」（使用者只好自己重整，還以為壞了），猜長了是白等。

    **就緒的判準是「port 連得上 **且** 我們的行程還活著」**，兩個條件缺一不可。
    只看 port 會被騙：這個判準的第一版就只檢查連線，測試時故意讓別的程式
    佔住 8000，結果那個程式也在 listen，於是輪詢判定「就緒」並開了瀏覽器——
    使用者會被帶到一個完全不相干的服務。行程還活著才是真正該問的問題。

    webbrowser 模組跨平台（Windows 走 os.startfile，macOS 走 open），
    所以這裡不需要為各平台各寫一份。
    """
    def _go():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return  # 服務已經退出（綁不上 port、匯入失敗…），不要開
            if port_in_use():
                break
            time.sleep(0.3)
        else:
            return
        try:
            webbrowser.open(URL)
        except Exception:
            pass  # 開不起來不影響服務，使用者自己貼網址就好

    threading.Thread(target=_go, daemon=True).start()


def start(ui, dev: bool = False) -> int:
    """啟動儀表板。

    `ui` 是輸出工具的集合（onboard.py 的 ok/warn/bad/explain/title），
    這樣訊息風格與安裝引導一致，不會一半彩色一半不是。
    """
    ui.title("啟動 Web 儀表板")

    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
    if not os.path.exists(VENV_PYTHON):
        # macOS 走 start-web.sh 進來時 ensure_venv 已經建好環境了，Windows 沒有
        # 對應物。與其在這裡再造一套建環境邏輯（那又是一份會漂移的實作），
        # 不如指回完整引導——它本來就負責這件事。
        ui.warn("找不到專案環境（.venv），先用目前的 Python 試試看")
        launcher = "chatpulse.bat" if IS_WINDOWS else "./chatpulse.sh"
        ui.arrow(f"若下面出現套件錯誤，跑一次完整引導建好環境：{launcher}")

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

    # ── 3. 連接埠 ────────────────────────────────────────────
    # 先問清楚再啟動。讓 uvicorn 自己去撞的話，使用者得到的是一行英文的
    # 「[Errno 48] address already in use」，而這其實是最常見的情況之一：
    # 上一次啟動的服務忘了關。
    available, reason = port_available()
    if not available and reason == "in_use":
        print()
        ui.bad(f"連接埠 {PORT} 已經被佔用，服務起不來")
        ui.arrow(f"多半是上一次啟動的還開著——先開 {URL} 看看是不是已經在跑了")
        ui.arrow("如果是，回到那個視窗按 Ctrl+C 再重試；不是的話請關掉佔用它的程式")
        if IS_WINDOWS:
            ui.arrow("Windows 補充：上次按 Ctrl+C 時如果跳出「終止批次工作 (Y/N)?」而你選了 Y，")
            ui.plain("       服務會變成孤兒繼續佔著 8000（下次請選 N 讓它正常收尾）。")
            ui.plain("       要清掉它：netstat -ano | findstr :8000 找出 PID，")
            ui.plain("       再跑 taskkill /PID <那個PID> /F")
        return 1
    if not available:
        print()
        ui.bad(f"連接埠 {PORT} 綁不上（系統拒絕，不是被別的程式佔用）")
        if IS_WINDOWS:
            ui.arrow("Windows 上常見原因是 Hyper-V／WSL2 保留了這段連接埠")
            ui.plain("       確認：netsh interface ipv4 show excludedportrange protocol=tcp")
            ui.plain("       若 8000 落在保留範圍內，請維護者把服務改用其他連接埠")
        else:
            ui.arrow("請確認是否有防火牆或權限設定擋住這個連接埠")
        return 1

    # ── 4. 啟動 ─────────────────────────────────────────────
    print()
    if have_ui:
        ui.ok(f"儀表板：{URL}")
        ui.plain("       瀏覽器會自動開啟；要停止服務請在這個視窗按 Ctrl+C")
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
        # 用 Popen 而不是 call，是為了讓開瀏覽器的執行緒拿得到行程物件——
        # 它要靠 proc.poll() 判斷服務是不是已經死了（見 _open_browser_when_ready）。
        proc = subprocess.Popen(args, cwd=BASE_DIR)
    except FileNotFoundError:
        ui.bad("啟動失敗：找不到 uvicorn",
               f'執行："{python}" -m pip install -r requirements.txt')
        return 1

    if have_ui:
        _open_browser_when_ready(proc)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C 已經由終端機送給整個行程群組，子行程會自己收尾，
        # 這裡只要等它走完，不要留下孤兒行程。
        try:
            return proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            proc.terminate()
            return 0
