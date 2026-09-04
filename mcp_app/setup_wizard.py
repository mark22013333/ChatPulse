"""MCP 安裝精靈：檢查環境、檢查憑證、跑 OAuth 授權、輸出 MCP 設定片段。

規格對應：SPECIFICATION.md 2.4、3.3（MCP 註冊路徑與 GOOGLE_API_KEY 環境變數）、4.2（Scope）。

授權時只要 `cfg.CHAT_SCOPES`（讀 Space／讀訊息／發訊息）——MCP 與 CLI 這兩個入口
不需要身分 scope。儀表板另需 `cfg.DASHBOARD_SCOPES`（含 userinfo），走它自己的登入流程。
"""

import os
import sys
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import config as cfg
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# 專案自帶的虛擬環境；存在時一律優先使用（版本已由 requirements.txt 鎖定）
VENV_PYTHON = os.path.join(BASE_DIR, ".venv", "bin", "python")

def print_banner():
    print("=" * 65)
    print(" 🚀 Google Chat MCP 助手 - 新手安裝與一鍵授權導引 (Setup Wizard)")
    print("=" * 65)

def check_python_and_uv():
    print("\n[步驟 1/4] 檢查執行環境...")
    if os.path.exists(VENV_PYTHON):
        print(f"✅ 找到專案虛擬環境: {VENV_PYTHON}")
    else:
        print("⚠️ 尚未建立專案虛擬環境。建議先執行：")
        print(f"   uv venv --python 3.12 {os.path.join(BASE_DIR, '.venv')}")
        print(f"   uv pip install -r {os.path.join(BASE_DIR, 'requirements.txt')}")

    uv_path = shutil.which("uv")
    if uv_path:
        print(f"✅ 找到 uv: {uv_path}")
    else:
        print("⚠️ 未檢測到 uv 套件管理器。建議安裝以獲得最佳執行速度：")
        print("   curl -LsSf https://astral.sh/uv/install.sh | sh")
    print("✅ Python 環境正常。")

def check_client_secret():
    print("\n[步驟 2/4] 檢查 Google OAuth Client Secret 憑證...")
    secret_path = cfg.CLIENT_SECRET_FILE
    if os.path.exists(secret_path):
        print(f"✅ 找到 Client Secret: {secret_path}")
        return True
    else:
        print(f"❌ 找不到 Client Secret！預期路徑: {secret_path}")
        print("\n📖 【同事取得憑證方式】：")
        print("  方式 A (最簡單)：向已建置好的同事（如 Mark）直接索取 client_secret.json，並放至：")
        print(f"         {secret_path}")
        print("  方式 B (自行建立)：請參考專案 SETUP_GUIDE.md 前往 GCP 建立 Desktop 類型 OAuth 憑證。")
        return False

def check_and_auth_token():
    print("\n[步驟 3/4] 執行個人帳號授權 (取得個人 Token)...")
    # core/config.py 現在的正式名稱是 LEGACY_TOKEN_FILE（TOKEN_FILE 只是相容別名）
    token_path = cfg.LEGACY_TOKEN_FILE
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, cfg.CHAT_SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        print(f"✅ 已檢測到有效 Token: {token_path}")
        print("   無需重新授權，隨時可直接使用！")
        return True
    elif creds and creds.expired and creds.refresh_token:
        try:
            print("🔄 Token 已過期，正在自動刷新...")
            creds.refresh(Request())
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
            print("✅ Token 刷新成功！")
            return True
        except Exception as e:
            print(f"⚠️ 自動刷新失敗 ({e})，準備重新登入...")

    # 觸發本地授權視窗
    print("\n👉 即將開啟瀏覽器跳出 Google 帳號授權頁面...")
    print("   請點選您的公司 Google Workspace 帳號，並同意授權。")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(cfg.CLIENT_SECRET_FILE, cfg.CHAT_SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
        print(f"\n🎉 授權成功！已為您產生個人專屬 Token: {token_path}")
        return True
    except Exception as e:
        print(f"\n❌ 授權過程發生錯誤: {e}")
        return False

def show_mcp_config_instructions():
    print("\n[步驟 4/4] 註冊至 Claude Desktop / Claude Code...")
    server_script = os.path.join(BASE_DIR, "mcp_app", "mcp_server.py")

    if os.path.exists(VENV_PYTHON):
        # 有 .venv 就直接用它：版本已鎖定，啟動也比每次解相依快
        command_line = f'''      "command": "{VENV_PYTHON}",
      "args": ["{server_script}"],'''
    else:
        uv_bin = shutil.which("uv") or "uv"
        command_line = f'''      "command": "{uv_bin}",
      "args": [
        "run",
        "--with", "mcp",
        "--with", "google-api-python-client",
        "--with", "google-auth-oauthlib",
        "--with", "google-auth-httplib2",
        "--with", "requests",
        "--with", "cryptography",
        "python3",
        "{server_script}"
      ],'''

    config_snippet = f'''{{
  "mcpServers": {{
    "google-chat": {{
{command_line}
      "env": {{
        "GOOGLE_API_KEY": "請填入你的 Gemini API key"
      }}
    }}
  }}
}}'''

    print("👉 請將以下 JSON 區塊複製到您的 MCP 設定檔中：\n")
    print(config_snippet)
    print("\n📍 設定檔位置：")
    print("   • Claude Desktop : ~/Library/Application Support/Claude/claude_desktop_config.json")
    print("   • Claude Code    : ~/.claude.json")
    print("\n⚠️ 關於 GOOGLE_API_KEY（規格 3.3）：")
    print("   • Claude Code 由終端啟動，會繼承 shell 環境，若已寫在 ~/.zshrc 可移除上面的 env 區塊")
    print("   • Claude Desktop 是 GUI 程式，不讀 ~/.zshrc，env 區塊必須保留並填入實際金鑰")
    print("=" * 65)
    print(" 💡 設定完成後，重啟 Claude 即可直接在對話中使用 Google Chat 工具！")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    print_banner()
    check_python_and_uv()
    if check_client_secret():
        if check_and_auth_token():
            show_mcp_config_instructions()
