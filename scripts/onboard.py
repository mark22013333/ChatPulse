"""ChatPulse 互動式安裝引導（跨平台）。

**為什麼邏輯放在 Python 而不是各寫一份 .sh 與 .bat**：兩份腳本必然漂移，
而 Windows 那份的坑維護者（用 macOS）永遠踩不到——會變成同事回報「壞了」、
你這邊怎麼試都是好的。Python 本來就是這個專案的執行環境，也本來就跨平台，
所以 `chatpulse.sh` 與 `chatpulse.bat` 只負責一件事：找到 Python 並執行本檔。

**設計原則**：每一步都先說「這一步在做什麼、為什麼需要」，再動作；失敗時
一定給出下一步該做什麼。使用者是同事，不是維護者——他不該需要讀原始碼
才知道自己卡在哪。
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import unicodedata

# 直接執行本檔時 sys.path[0] 就是 scripts/，但被 import 或用 -m 執行時未必，
# 所以明確補上——webapp 就在隔壁。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webapp  # noqa: E402  （要先補好 sys.path 才 import 得到）

# 引導流程整段都在跟子行程（pip / npm / uvicorn / 授權精靈）交錯輸出，
# 不解掉緩衝的話訊息順序會亂到看不懂。必須在任何輸出之前做。
webapp.unbuffer_output()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IS_WINDOWS = platform.system() == "Windows"

# Windows 的 venv 執行檔在 Scripts\ 而不是 bin/
VENV_DIR = os.path.join(BASE_DIR, ".venv")
VENV_PYTHON = os.path.join(
    VENV_DIR, "Scripts" if IS_WINDOWS else "bin", "python.exe" if IS_WINDOWS else "python"
)
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CLIENT_SECRET = os.path.join(CONFIG_DIR, "client_secret.json")
REQUIREMENTS = os.path.join(BASE_DIR, "requirements.txt")

TOTAL_STEPS = 5


# ----------------------------------------------------------------------
# 輸出工具
# ----------------------------------------------------------------------

def _supports_colour() -> bool:
    """Windows 舊版 cmd 不吃 ANSI 色碼，吐出來會是一堆亂碼字元。"""
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if IS_WINDOWS:
        # Windows 10 1511 之後的 cmd／Terminal 支援，但無法可靠偵測版本，
        # 所以只在明確是新版終端時才上色
        return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM"))
    return True


_C = _supports_colour()


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _C else text


def title(text: str) -> None:
    line = "=" * 64
    print(f"\n{c('36', line)}")
    print(c("36;1", f" {text}"))
    print(f"{c('36', line)}\n")


def step(n: int, text: str) -> None:
    print(f"\n{c('36;1', f'[步驟 {n}/{TOTAL_STEPS}]')} {c('1', text)}")


def _sym(fancy: str, plain: str) -> str:
    """終端機編不了這個符號時退回 ASCII。

    輸出串流設了 errors="replace"（不然 cp950 主控台印中文會直接中止），
    但那會把 ✓ 與 ✗ **同時**變成 `?`——成功與失敗長得一模一樣，比沒有符號
    還糟。所以先問這個編碼吃不吃得下，吃不下就換 [OK] / [X]。
    """
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        fancy.encode(enc)
        return fancy
    except (UnicodeEncodeError, LookupError):
        return plain


def ok(text: str) -> None:
    print(f"  {c('32', _sym('✓', '[OK]'))} {text}")


def warn(text: str) -> None:
    print(f"  {c('33', '!')} {text}")


def bad(text: str, nextstep: str = "") -> None:
    print(f"  {c('31', _sym('✗', '[X]'))} {text}")
    if nextstep:
        print(f"    {c('36', _sym('→', '->'))} {nextstep}")


def explain(text: str) -> None:
    """教學文字。每一步的「這是什麼、為什麼」都走這裡。"""
    for line in text.strip().splitlines():
        print(f"    {c('90', line.strip())}")


def plain(text: str) -> None:
    """不帶符號的補充行，用在需要對齊上一行的續行。"""
    print(text)


def arrow(text: str) -> None:
    """下一步指示。與 bad() 的 → 同一個視覺語彙。"""
    print(f"    {c('36', _sym('→', '->'))} {text}")


class _UI:
    """把輸出工具打包給 webapp 用，讓兩邊的訊息風格一致。"""

    title = staticmethod(title)
    ok = staticmethod(ok)
    warn = staticmethod(warn)
    bad = staticmethod(bad)
    explain = staticmethod(explain)
    plain = staticmethod(plain)
    arrow = staticmethod(arrow)


_IME_HINT = "輸入法可能還停在中文模式，請切回英數再輸入一次（注音的 3 鍵送出的是「ˇ」）。"


def _read_line(prompt: str):
    """讀一行輸入。正常回傳字串；那一行解不了碼時回傳 None（不拋例外）。

    **為什麼要吞 UnicodeDecodeError**：使用者是台灣的同事，注音是預設輸入法，
    忘了切回英數是家常便飯而不是意外。注音鍵盤的 3 鍵送出的是上聲符號「ˇ」
    （U+02C7，UTF-8 是 CB 87）。只要這個字元在送達前掉了半截——例如按 Backspace
    想刪掉它，而 macOS 的終端機行編輯沒有 IUTF8、一次只刪**一個位元組**——
    那一行就會留下孤兒的 0xCB，input() 用 strict 解碼直接拋 UnicodeDecodeError。

    使用者畫面上只看得到自己打的「3」，卻收到一整串 traceback、整個安裝中止，
    完全無從得知發生什麼事——而他其實只要切回英數重打一次就好。所以這裡一定要接住。
    EOFError 與 KeyboardInterrupt 不在這裡處理，照原樣往上丟給呼叫端。
    """
    try:
        return input(prompt)
    except UnicodeDecodeError:
        return None


def _looks_like_ime(text: str) -> bool:
    """這串輸入看起來是不是中文輸入法造成的（含非 ASCII 字元）。

    用來決定要說「請輸入 1 到 3」還是「請切回英數」——使用者打了「ˇ」卻被叫去
    「輸入 1 到 3 之間的數字」，只會以為自己按錯鍵，找不到真正的原因。
    """
    return any(ord(ch) > 127 for ch in text)


def pause(prompt: str = "按 Enter 繼續") -> None:
    try:
        # 這裡不在意內容、只在意「使用者按了 Enter」，所以解不了碼也照樣繼續，
        # 不需要叫他重按一次（那一行既然送達，Enter 就是按過了）。
        _read_line(f"\n  {c('90', prompt)}… ")
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = _read_line(f"\n  {prompt} [{hint}]： ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(1)
        if raw is None:
            print(f"  讀不到你的輸入——{_IME_HINT}")
            continue
        raw = raw.strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        if _looks_like_ime(raw):
            print(f"  收到的不是 y 或 n——{_IME_HINT}")
        else:
            print("  請輸入 y 或 n。")


def ask_choice(prompt: str, options: list) -> int:
    """options 是 [(標籤, 說明), …]，回傳選中的索引。"""
    print()
    for idx, (label, desc) in enumerate(options, 1):
        print(f"  {c('36;1', str(idx))}) {c('1', label)}")
        if desc:
            print(f"     {c('90', desc)}")
    while True:
        try:
            raw = _read_line(f"\n  {prompt} [1-{len(options)}]： ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(1)
        if raw is None:
            print(f"  讀不到你的輸入——{_IME_HINT}")
            continue
        # 中文輸入法在全形模式下打出的是「３」，NFKC 會把它正規化回半形 3。
        # 用標準正規化而不是自己列對照表，才不會漏掉其他形式的數字。
        raw = unicodedata.normalize("NFKC", raw).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        if _looks_like_ime(raw):
            print(f"  收到的不是半形數字——{_IME_HINT}")
        else:
            print(f"  請輸入 1 到 {len(options)} 之間的數字。")


def run(args: list, **kwargs) -> int:
    """執行子行程並讓輸出直接顯示。回傳 exit code，不拋例外。"""
    try:
        return subprocess.call(args, cwd=BASE_DIR, **kwargs)
    except FileNotFoundError:
        return 127
    except KeyboardInterrupt:
        return 130


# ----------------------------------------------------------------------
# 步驟
# ----------------------------------------------------------------------

def step_welcome() -> None:
    title("ChatPulse 安裝引導")
    print("  這個工具幫你處理 Google Chat 的兩件事：\n")
    print(f"    {c('36', '•')} 把群組對話整理成結構化摘要（重點、決議、待辦）")
    print(f"    {c('36', '•')} 找出跨所有群組「誰 @ 了你」，並產出可直接送出的回話草稿\n")
    explain(
        """
        讀寫 Google Chat 用的是你本人的 Google 帳號，不是機器人。
        你看得到什麼，完全由你在 Google Chat 的群組成員身分決定——
        沒加入的群組，這個工具也讀不到。
        送出任何訊息前都會再問你一次。
        """
    )
    print(f"\n  作業系統：{c('1', platform.system())} {platform.release()}")
    print(f"  專案位置：{c('1', BASE_DIR)}")
    pause("準備好就按 Enter 開始")


def step_python() -> bool:
    step(1, "檢查 Python 環境")
    explain(
        """
        這個工具用 Python 3.12 執行。這一步會建立一個獨立的環境（.venv），
        套件都裝在裡面，不會動到你電腦上其他的 Python。
        """
    )

    if os.path.exists(VENV_PYTHON):
        out = subprocess.run(
            [VENV_PYTHON, "--version"], capture_output=True, text=True
        )
        ok(f"已有專案環境（{out.stdout.strip() or out.stderr.strip()}）")
    else:
        warn("還沒有專案環境，現在建立（第一次會下載套件，約 1~2 分鐘）")
        uv = shutil.which("uv") or (
            os.path.expanduser("~/.local/bin/uv") if not IS_WINDOWS else None
        )
        if uv and os.path.exists(uv if os.path.isabs(uv) else shutil.which("uv") or ""):
            code = run([uv, "venv", "--python", "3.12", VENV_DIR])
            if code == 0:
                code = run([uv, "pip", "install", "--python", VENV_PYTHON, "-r", REQUIREMENTS])
        else:
            warn("找不到 uv，改用 Python 內建的 venv")
            code = run([sys.executable, "-m", "venv", VENV_DIR])
            if code == 0:
                pip = os.path.join(
                    VENV_DIR, "Scripts" if IS_WINDOWS else "bin",
                    "pip.exe" if IS_WINDOWS else "pip",
                )
                code = run([pip, "install", "-r", REQUIREMENTS])

        if code != 0 or not os.path.exists(VENV_PYTHON):
            bad(
                "環境建立失敗",
                "確認網路可用，或手動執行：python3.12 -m venv .venv 後裝 requirements.txt",
            )
            return False
        ok("專案環境建立完成")

    probe = subprocess.run(
        [VENV_PYTHON, "-c", "import fastapi, uvicorn, mcp, cryptography, PIL"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        bad(
            "必要套件不完整",
            f'執行："{VENV_PYTHON}" -m pip install -r requirements.txt',
        )
        explain(f"原始錯誤：{(probe.stderr or '').strip()[:200]}")
        return False
    ok("必要套件都在")

    # 潤稿規則屬於「安裝完整性」而不是「這台電腦裝了什麼」，所以跟套件檢查
    # 放在同一步：它跟著 repo 版控，會缺就是 clone／解壓不完整。
    report_polish_state()
    return True


_POLISH_PROBE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from core.polishers.sepia import rules_available, rules_version\n"
    "avail, reason = rules_available()\n"
    "info = rules_version()\n"
    "print('%s|%s|%s' % ('OK' if avail else 'NG', info.get('version') or '', reason))\n"
)


def polish_state() -> tuple:
    """回傳 (狀態, 版本, 原因)。狀態為 'ok' / 'missing' / 'unknown'。

    **這裡檢查的不是「有沒有裝 Sepia 這個 Claude Code skill」。** Sepia 的規則是
    以純文字 vendored 進本 repo 的（`core/polishers/sepia_rules/`），使用者不需要
    安裝任何東西——理由見 ADR-0007：要讓 CLI 去載入 skill 就得拆掉本專案的成本
    控制旗標，還會讓草稿偷偷受各人本機的 CLAUDE.md 影響。

    所以這一項會失敗的唯一原因是「檔案沒跟著專案一起下來」（zip 解壓不完整、
    產物被清掉）。訊息要照這個事實寫，不要叫使用者去裝一個不存在的東西。
    """
    if not os.path.exists(VENV_PYTHON):
        return "unknown", "", "還沒有 Python 環境"
    probe = subprocess.run(
        [VENV_PYTHON, "-c", _POLISH_PROBE, BASE_DIR], capture_output=True, text=True
    )
    out = (probe.stdout or "").strip().splitlines()
    if not out or "|" not in out[-1]:
        err = (probe.stderr or "").strip().splitlines()
        return "unknown", "", (err[-1] if err else "潤稿規則檢查沒有回應")
    status, _, rest = out[-1].partition("|")
    version, _, reason = rest.partition("|")
    return ("ok" if status == "OK" else "missing"), version, reason


def report_polish_state() -> str:
    """印出潤稿規則的檢查結果，回傳狀態字串給呼叫端計數。

    兩個入口（引導的步驟 1 與 check 子指令）共用同一份判斷與同一段文字——
    分開寫的話遲早會出現「引導說沒問題、check 說缺檔」這種自相矛盾。
    """
    state, version, reason = polish_state()
    if state == "ok":
        ok(f"Sepia 潤稿規則已就位{f'（v{version}）' if version else ''}")
    elif state == "missing":
        warn("找不到 Sepia 潤稿規則——只有〈建議回話〉的「潤稿」選項會不能用")
        arrow("這份規則隨專案版控，在專案目錄執行：git restore core/polishers/sepia_rules")
        if reason:
            explain(f"原始原因：{reason}")
    else:
        warn(f"潤稿規則檢查不了：{reason}")
        arrow("先把上面的 Python 環境問題解決，再跑一次")
    return state


def step_credentials() -> bool:
    step(2, "Google 憑證")
    explain(
        """
        這個工具要用你的身分讀寫 Google Chat，所以需要兩份東西：

          client_secret.json  團隊共用的「應用程式識別」——向維護者索取，
                              大家用同一份，不含任何人的個人資料。
          個人 Token          下一步授權時自動產生，只屬於你，
                              絕對不要複製別人的（那會讀到別人的訊息）。
        """
    )

    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CLIENT_SECRET):
        ok("client_secret.json 已就位")
        return True

    bad("找不到 client_secret.json")
    print(f"\n    請把維護者給你的檔案放到這個位置（目錄我已經建好了）：")
    print(f"    {c('1', CLIENT_SECRET)}\n")
    explain(
        """
        放好之後重新執行這個引導即可。
        想自己在 Google Cloud 建一份的話，步驟見 SETUP_GUIDE.md 的 Q1。
        """
    )
    return False


_AUTH_PROBE = (
    "import json,sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from core import config as cfg\n"
    "d = json.load(open(cfg.LEGACY_TOKEN_FILE))\n"
    "have = set(d.get('scopes') or [])\n"
    "print('%d|%s' % (len(set(cfg.DASHBOARD_SCOPES) - have), d.get('account') or ''))\n"
)


def auth_state() -> tuple:
    """回傳 (狀態, 帳號)。狀態為 'ok' / 'partial'（權限不足）/ 'none' / 'unreadable'。"""
    token = os.path.join(CONFIG_DIR, "google_chat_token.json")
    if not os.path.exists(token) or not os.path.exists(VENV_PYTHON):
        return "none", ""
    probe = subprocess.run(
        [VENV_PYTHON, "-c", _AUTH_PROBE, BASE_DIR], capture_output=True, text=True
    )
    out = (probe.stdout or "").strip().splitlines()
    if not out or "|" not in out[-1]:
        return "unreadable", ""
    missing, _, account = out[-1].partition("|")
    if not missing.isdigit():
        return "unreadable", ""
    return ("ok" if missing == "0" else "partial"), account


def step_authorise(force: bool = False) -> bool:
    """`force=True` 是使用者明講要重新授權（auth 子指令），那就別自作聰明跳過。"""
    step(3, "用你的 Google 帳號授權")

    state, account = auth_state()
    if state == "ok" and not force:
        # 授權過了就不要再問。以前這裡不管三七二十一都問一次
        # 「現在開始授權？」，於是每跑一次引導就像要重登一次——
        # 實際上憑證會自己續期，使用者只是被問了而已。
        ok(f"已完成授權{f'（{account}）' if account else ''}")
        explain(
            """
            授權只要做一次。憑證存在你自己的電腦上，過期時程式會自動用
            refresh token 換新的，不會再要你登入。
            要換 Google 帳號或重新授權，才需要單獨跑 auth 子指令。
            """
        )
        return True
    if state == "partial":
        warn("之前的授權少了儀表板需要的權限，要重做一次（這次會問到六項）")
    elif state == "unreadable":
        warn("既有的授權檔讀不出來，重做一次比較快")

    explain(
        """
        接下來會開啟瀏覽器，請選你的公司 Google 帳號並同意授權。

        同意畫面會要求六項權限：三項是讀寫 Google Chat，
        另三項是「知道你是誰」——那是判斷「誰 @ 了你」的必要條件。
        授權結果只存在你自己的電腦上（config/google_chat_token.json），
        而且**只要做這一次**。
        """
    )
    if not ask_yes("現在開始授權？"):
        warn("已跳過。之後可以單獨執行這一步（見最後的說明）")
        return False

    code = run([VENV_PYTHON, os.path.join(BASE_DIR, "mcp_app", "setup_wizard.py")])
    if code != 0:
        bad("授權未完成", "重新執行這個引導再試一次；若瀏覽器沒開起來，請檢查是否被防火牆擋住")
        return False
    return True


def step_choose_entry() -> bool:
    """選入口。回傳「是否要啟動儀表板」，實際啟動由呼叫端延到最後。

    延後的理由：uvicorn 會佔住這個視窗直到 Ctrl+C，排在它後面的說明
    使用者永遠看不到——包括「以後要怎麼再啟動」這種最需要看到的東西。
    """
    step(4, "你的入口")

    mcp_ok = mcp_registered()
    ui_ok = webapp.frontend_state() != "missing"

    # 已經裝好的就不要再問一次。這一步是**一次性的安裝設定**，
    # 每跑一次引導就問一次「你想用哪個」，等於在問使用者
    # 「你上次裝的還算數嗎」——他每次都得重新讀一遍三個選項才能回答。
    if mcp_ok:
        ok("Claude Code 入口已就緒（MCP 已註冊）")
    if ui_ok:
        ok("Web 儀表板入口已就緒（畫面已備妥）")

    if mcp_ok and ui_ok:
        explain(
            """
            兩個入口都可以用了。
            Claude Code：在對話裡直接說「摘要某個群組」。
            Web 儀表板：有 Mention 收件匣與回話草稿。
            """
        )
        return ask_yes("現在啟動 Web 儀表板？", default=False)

    explain(
        """
        兩個入口用的是同一套功能，差別在你想在哪裡操作。
        不確定的話選第 1 個——它最輕，之後隨時可以再裝另一個。
        """
    )
    idx = ask_choice(
        "你想用哪個",
        [
            (
                "在 Claude Code 裡用（推薦，最輕）"
                + ("　※ 已裝好" if mcp_ok else ""),
                "在對話中直接說「摘要某個群組」。",
            ),
            (
                "Web 儀表板（功能完整）" + ("　※ 已備妥" if ui_ok else ""),
                "瀏覽器介面，有 Mention 收件匣與回話草稿。畫面已經隨專案附上，開了就能用。",
            ),
            ("兩個都裝", "先裝 MCP，再啟動儀表板。"),
        ],
    )

    if idx in (0, 2):
        _install_mcp()
    return idx in (1, 2)


MCP_NAME = "google-chat"


def mcp_registered() -> bool:
    """`google-chat` 這個 MCP 是不是已經註冊進 Claude Code 了。

    優先讀 `~/.claude.json`：`claude mcp list` 會對每一台 MCP server 做健康
    檢查，實測要 7 秒，放進引導流程等於每跑一次就卡 7 秒。讀檔是毫秒級。

    那個檔案的格式是 Claude Code 的內部細節、可能會變，所以讀不到時退回
    CLI；CLI 也不行就當作沒註冊——寧可多問一次，也不要誤判成「已裝好」
    而讓使用者以為自己裝過了。
    """
    config = os.path.expanduser("~/.claude.json")
    try:
        with open(config, "r", encoding="utf-8") as fh:
            if MCP_NAME in (json.load(fh).get("mcpServers") or {}):
                return True
    except (OSError, ValueError, AttributeError):
        pass
    except Exception:
        pass

    claude = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude:
        return False
    try:
        out = subprocess.run(
            [claude, "mcp", "list"], capture_output=True, text=True, timeout=20
        )
        return MCP_NAME in (out.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


def _windows_path_hazard() -> str:
    """回傳專案路徑中會讓 Windows 的 .cmd 呼叫出錯的字元（沒有就回空字串）。

    Claude Code 在 Windows 上是 `claude.cmd`，而 subprocess 執行 .cmd 目標時
    會隱式經過 cmd.exe——引數等於被解析兩次。路徑裡一個 `&` 就會讓 cmd 在那裡
    把命令切成兩段，後半段變成另一條指令去執行；`C:\\Users\\me\\R&D\\proj`
    這種資料夾名在研發單位並不罕見。`%VAR%` 形態與 `^` 也會出事。

    macOS 上 `claude` 是真正的執行檔，不會有二次解析，所以維護者踩不到——
    又是一個「只有 Windows 使用者會遇到」的坑，所以寧可先講。
    """
    if not IS_WINDOWS:
        return ""
    return "".join(sorted({ch for ch in BASE_DIR if ch in '&|<>^%'}))


def _install_mcp() -> None:
    print()
    hazard = _windows_path_hazard()
    if hazard:
        warn(f"專案路徑含特殊字元 {hazard}，Windows 上註冊 MCP 可能會失敗")
        explain(
            f"""
            這些字元對 Windows 的命令列有特殊意義，而 claude 指令在 Windows 上
            是批次檔，路徑會被多解析一次。若下面的註冊失敗，把整個專案搬到
            不含這些字元的路徑（例如 C:\\ChatPulse）再跑一次就好。
            目前路徑：{BASE_DIR}
            """
        )
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude:
        bad(
            "找不到 claude 指令",
            "請先安裝 Claude Code 並登入（終端輸入 claude 能進對話即可），再重新執行這個引導",
        )
        explain(
            """
            摘要功能預設使用你本機 Claude Code 的登入狀態，
            所以不需要任何 API key——但前提是 Claude Code 裝好了。
            """
        )
        return

    ok(f"找到 Claude Code（{claude}）")
    server = os.path.join(BASE_DIR, "mcp_app", "mcp_server.py")
    run([claude, "mcp", "remove", "google-chat"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    code = run([claude, "mcp", "add", "--scope", "user", "google-chat", "--",
                VENV_PYTHON, server])
    if code == 0:
        ok("已註冊進 Claude Code")
        print()
        explain(
            """
            重啟 Claude Code 後就可以直接問它：
              「幫我列出 Google Chat 有哪些群組」
              「摘要『0.暫存』最近 50 則對話」
            """
        )
    elif hazard:
        bad("自動註冊失敗",
            f"很可能就是路徑裡的 {hazard} 造成的——把專案搬到不含這些字元的路徑再試一次")
    else:
        bad("自動註冊失敗", "改用手動設定，步驟見 SETUP_GUIDE.md")


def _start_dashboard(ask_first: bool = True, dev: bool = False) -> None:
    """啟動儀表板。實際流程在 scripts/webapp.py，兩個平台共用同一份。

    這裡只多做一件事：在**引導流程中**遇到「沒畫面又建不出來」時直接擋下來。
    那時使用者還站在岔路口，告訴他改走 Claude Code 入口比啟動一個沒有畫面的
    API 有用得多。單獨執行 `web` 子指令時不擋——那是他明確指定要 API。
    """
    print()
    if webapp.frontend_state() == "missing" and not webapp.find_npm():
        bad("這台電腦沒辦法開儀表板畫面")
        explain(
            """
            版控裡沒有前端建置產物，而這台電腦也沒有 Node.js 可以建置。
            （正常情況下 clone 就會有產物，會走到這裡通常是產物被清掉了。）
            """
        )
        print()
        arrow("最省事：改用 Claude Code 入口，功能一樣，不需要 Node.js")
        arrow("或安裝 Node.js 後重跑這個引導：https://nodejs.org/")
        return

    explain(
        """
        啟動後瀏覽器會自動打開 http://localhost:8000。
        要停止服務，回到這個視窗按 Ctrl+C。
        """
    )
    if ask_first and not ask_yes("現在啟動儀表板？"):
        warn("已跳過。之後可以單獨啟動（見最後的說明）")
        return

    webapp.start(_UI, dev=dev)


def step_done(authorised: bool) -> None:
    step(5, "完成")
    print("  以後要用的話：\n")
    # 兩個平台講同一套子指令。以前這裡 macOS 教 scripts/*.sh、Windows 教
    # chatpulse.bat，等於同一件事有兩種說法，文件與口頭支援都得講兩遍。
    launcher = "chatpulse.bat" if IS_WINDOWS else "./chatpulse.sh"
    width = len(launcher) + 8
    for suffix, desc in (
        ("web", "啟動 Web 儀表板（日常最常用的就這個）"),
        ("check", "檢查安裝狀態"),
        ("auth", "重新授權（換 Google 帳號時）"),
        ("mcp", "重新註冊 Claude Code 的 MCP 入口"),
        ("", "再跑一次完整引導（重裝或重新設定時才需要）"),
    ):
        cmd = f"{launcher} {suffix}".strip()
        print(f"    {c('1', cmd.ljust(width))}  {desc}")

    if not authorised:
        print()
        warn("你還沒完成 Google 授權，摘要與收件匣功能會不能用")

    print()
    explain(
        """
        遇到問題時：SETUP_GUIDE.md 的 FAQ 收了目前已知會卡住的情況，
        包含「送出的訊息旁邊為什麼有個灰標籤」這種一定會被問到的事。
        """
    )
    print()


# ----------------------------------------------------------------------

def cmd_check() -> int:
    """只做檢查，不安裝。Windows 沒有 doctor.sh，用這個代替。"""
    title("ChatPulse 安裝檢查")
    problems = 0
    warnings = 0

    if os.path.exists(VENV_PYTHON):
        ok("Python 環境存在")
        probe = subprocess.run(
            [VENV_PYTHON, "-c", "import fastapi, uvicorn, mcp, cryptography, PIL"],
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            ok("必要套件都在")
        else:
            bad("套件不完整", f'"{VENV_PYTHON}" -m pip install -r requirements.txt')
            problems += 1
    else:
        bad("沒有 Python 環境", "執行 chatpulse.bat（Windows）或 ./chatpulse.sh")
        problems += 1

    if os.path.exists(CLIENT_SECRET):
        ok("client_secret.json 已就位")
    else:
        bad("缺少 client_secret.json", f"向維護者索取後放到 {CONFIG_DIR}")
        problems += 1

    # 與步驟 3 共用同一個判斷（auth_state），不要各寫一份——
    # 兩邊對「授權算不算完整」的標準一旦分歧，就會出現
    # 「check 說沒問題但引導又叫你重登」這種自相矛盾。
    launcher = "chatpulse.bat" if IS_WINDOWS else "./chatpulse.sh"
    state, account = auth_state()
    if state == "ok":
        ok(f"已完成授權，權限完整{f'（{account}）' if account else ''}")
    elif state == "partial":
        warnings += 1
        warn("授權缺少儀表板需要的權限（Web 儀表板會不能用）")
        arrow(f"重新授權即可：{launcher} auth")
    elif state == "unreadable":
        problems += 1
        bad("授權檔讀不出來", f"刪掉 config/google_chat_token.json 後跑 {launcher} auth")
    else:
        problems += 1
        bad("還沒完成 Google 授權", f"執行 {launcher} auth")

    if os.path.exists(VENV_PYTHON):
        probe = subprocess.run(
            [VENV_PYTHON, "-c",
             "import sys;sys.path.insert(0,sys.argv[1]);"
             "from core import providers;"
             "print('|'.join(d['name'] for d in providers.describe_all() if d['available']))",
             BASE_DIR],
            capture_output=True, text=True,
        )
        avail = (probe.stdout or "").strip()
        if avail:
            ok(f"AI 供應商可用：{avail.replace('|', '、')}")
        else:
            bad(
                "沒有可用的 AI 供應商（摘要會失敗）",
                "最省事：裝好並登入 Claude Code，它用你現有的訂閱，不需要 API key",
            )
            problems += 1

        # 潤稿規則。只影響〈建議回話〉的「潤稿」選項（預設關閉），
        # 所以缺檔是 warning 不是 problem——不該讓沒在用潤稿的人看到紅字。
        if report_polish_state() != "ok":
            warnings += 1

    # 儀表板畫面。只影響 Web 入口，所以再糟也只是 warning——
    # 只用 Claude Code 的人不該因為這一項看到紅字。
    state = webapp.frontend_state()
    if state == "ready":
        ok("儀表板畫面已備妥")
    elif state == "stale":
        warnings += 1
        warn("儀表板畫面比前端原始碼舊（下次啟動會自動重建，需要 npm）")
    elif webapp.find_npm():
        warnings += 1
        warn("儀表板畫面尚未建置（下次啟動 web 時會自動建置）")
    else:
        warnings += 1
        warn("儀表板畫面不存在，且這台沒有 Node.js——Web 入口不能用，Claude Code 入口不受影響")

    if IS_WINDOWS:
        warnings += 1
        warn("Windows 上檔案權限保護不生效（這是作業系統差異，不是設定錯誤）")
        explain(
            """
            config\\ 與 data\\ 裡有你的 Google 授權憑證。POSIX 的 0600 權限
            在 Windows 上沒有對應，所以同一台電腦的其他帳號可能讀得到。
            請不要在共用電腦上使用，也不要把這兩個目錄放進 OneDrive 之類的同步夾。
            """
        )

    print()
    if problems:
        print(f"  {c('31;1', f'有 {problems} 項要處理')}——照上面每個 → 的指示做完再跑一次。\n")
    elif warnings:
        print(f"  {c('33;1', '可以用了，但上面有提醒要看一下。')}\n")
    else:
        print(f"  {c('32;1', '全部通過，可以開始用了。')}\n")
    return 1 if problems else 0


def main() -> int:
    argv = [a.strip().lower() for a in sys.argv[1:]]
    dev = "--dev" in argv
    positional = [a for a in argv if not a.startswith("-")]
    arg = positional[0] if positional else ""

    if arg in ("check", "doctor"):
        return cmd_check()
    if arg == "auth":
        # 明講要重新授權就照做，不要因為「已經授權過」而跳過——
        # 會下這個子指令的人多半正是要換帳號或修權限
        return 0 if step_authorise(force=True) else 1
    if arg == "mcp":
        # 同理：明講要裝 MCP 就直接裝（重跑會覆蓋既有註冊，是安全的）
        _install_mcp()
        return 0
    if arg == "web":
        # 明確指定要 web 就不擋——即使沒有畫面，API 本身仍然可用，
        # 缺什麼由 webapp 在視窗裡講清楚。
        return webapp.start(_UI, dev=dev)

    step_welcome()
    if not step_python():
        return 1
    if not step_credentials():
        return 1
    authorised = step_authorise()
    want_dashboard = step_choose_entry()
    step_done(authorised)
    if want_dashboard:
        _start_dashboard(ask_first=False, dev=dev)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中斷。")
        sys.exit(130)
