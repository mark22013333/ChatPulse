"""ChatPulse 互動式安裝引導（跨平台）。

**為什麼邏輯放在 Python 而不是各寫一份 .sh 與 .bat**：兩份腳本必然漂移，
而 Windows 那份的坑維護者（用 macOS）永遠踩不到——會變成同事回報「壞了」、
你這邊怎麼試都是好的。Python 本來就是這個專案的執行環境，也本來就跨平台，
所以 `chatpulse.sh` 與 `chatpulse.bat` 只負責一件事：找到 Python 並執行本檔。

**設計原則**：每一步都先說「這一步在做什麼、為什麼需要」，再動作；失敗時
一定給出下一步該做什麼。使用者是同事，不是維護者——他不該需要讀原始碼
才知道自己卡在哪。
"""

import os
import platform
import shutil
import subprocess
import sys

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


def ok(text: str) -> None:
    print(f"  {c('32', '✓')} {text}")


def warn(text: str) -> None:
    print(f"  {c('33', '!')} {text}")


def bad(text: str, nextstep: str = "") -> None:
    print(f"  {c('31', '✗')} {text}")
    if nextstep:
        print(f"    {c('36', '→')} {nextstep}")


def explain(text: str) -> None:
    """教學文字。每一步的「這是什麼、為什麼」都走這裡。"""
    for line in text.strip().splitlines():
        print(f"    {c('90', line.strip())}")


def plain(text: str) -> None:
    """不帶符號的補充行，用在需要對齊上一行的續行。"""
    print(text)


def arrow(text: str) -> None:
    """下一步指示。與 bad() 的 → 同一個視覺語彙。"""
    print(f"    {c('36', '→')} {text}")


class _UI:
    """把輸出工具打包給 webapp 用，讓兩邊的訊息風格一致。"""

    title = staticmethod(title)
    ok = staticmethod(ok)
    warn = staticmethod(warn)
    bad = staticmethod(bad)
    explain = staticmethod(explain)
    plain = staticmethod(plain)
    arrow = staticmethod(arrow)


def pause(prompt: str = "按 Enter 繼續") -> None:
    try:
        input(f"\n  {c('90', prompt)}… ")
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"\n  {prompt} [{hint}]： ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(1)
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
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
            raw = input(f"\n  {prompt} [1-{len(options)}]： ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(1)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
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
    return True


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


def step_authorise() -> bool:
    step(3, "用你的 Google 帳號授權")
    explain(
        """
        接下來會開啟瀏覽器，請選你的公司 Google 帳號並同意授權。

        同意畫面會要求六項權限：三項是讀寫 Google Chat，
        另三項是「知道你是誰」——那是判斷「誰 @ 了你」的必要條件。
        授權結果只存在你自己的電腦上（config/google_chat_token.json）。
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
    step(4, "選擇你要用哪個入口")
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
                "在 Claude Code 裡用（推薦，最輕）",
                "在對話中直接說「摘要某個群組」。",
            ),
            (
                "Web 儀表板（功能完整）",
                "瀏覽器介面，有 Mention 收件匣與回話草稿。畫面已經隨專案附上，開了就能用。",
            ),
            ("兩個都裝", "先裝 MCP，再啟動儀表板。"),
        ],
    )

    if idx in (0, 2):
        _install_mcp()
    return idx in (1, 2)


def _install_mcp() -> None:
    print()
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
        ("", "再跑一次這個引導（隨時可重複執行）"),
        ("web", "只啟動 Web 儀表板"),
        ("check", "檢查安裝狀態"),
        ("auth", "只重新授權"),
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

    token = os.path.join(CONFIG_DIR, "google_chat_token.json")
    if os.path.exists(token) and os.path.exists(VENV_PYTHON):
        probe = subprocess.run(
            [VENV_PYTHON, "-c",
             "import json,sys;sys.path.insert(0,r'%s');"
             "from core import config as cfg;"
             "have=set(json.load(open(cfg.LEGACY_TOKEN_FILE)).get('scopes') or []);"
             "print(len(set(cfg.DASHBOARD_SCOPES)-have))" % BASE_DIR],
            capture_output=True, text=True,
        )
        missing = (probe.stdout or "").strip()
        if missing == "0":
            ok("已完成授權，權限完整")
        elif missing.isdigit():
            warn(f"授權缺少 {missing} 個權限，Web 儀表板會不能用（重新授權即可）")
            warnings += 1
        else:
            bad("Token 讀不出來", "刪掉 config/google_chat_token.json 後重新授權")
            problems += 1
    else:
        bad("還沒完成 Google 授權", "執行引導的步驟 3")
        problems += 1

    if os.path.exists(VENV_PYTHON):
        probe = subprocess.run(
            [VENV_PYTHON, "-c",
             "import sys;sys.path.insert(0,r'%s');"
             "from core import providers;"
             "print('|'.join(d['name'] for d in providers.describe_all() if d['available']))"
             % BASE_DIR],
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
        return 0 if step_authorise() else 1
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
