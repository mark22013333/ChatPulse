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

def print_banner():
    print("=" * 65)
    print(" 🚀 Google Chat MCP 助手 - 新手安裝與一鍵授權導引 (Setup Wizard)")
    print("=" * 65)

def check_python_and_uv():
    print("\n[步驟 1/4] 檢查執行環境...")
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
    token_path = cfg.TOKEN_FILE
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
    uv_bin = shutil.which("uv") or "uv"
    server_script = os.path.join(BASE_DIR, "mcp_app", "mcp_server.py")

    config_snippet = f'''{{
  "mcpServers": {{
    "google-chat": {{
      "command": "{uv_bin}",
      "args": [
        "run",
        "--with", "mcp",
        "--with", "google-api-python-client",
        "--with", "google-auth-oauthlib",
        "--with", "google-auth-httplib2",
        "--with", "requests",
        "python3",
        "{server_script}"
      ]
    }}
  }}
}}'''

    print("👉 請將以下 JSON 區塊複製到您的 MCP 設定檔中：\n")
    print(config_snippet)
    print("\n📍 設定檔位置：")
    print("   • Claude Desktop : ~/Library/Application Support/Claude/claude_desktop_config.json")
    print("   • Claude Code    : ~/.claude.json")
    print("=" * 65)
    print(" 💡 設定完成後，重啟 Claude 即可直接在對話中使用 Google Chat 工具！")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    print_banner()
    check_python_and_uv()
    if check_client_secret():
        if check_and_auth_token():
            show_mcp_config_instructions()
