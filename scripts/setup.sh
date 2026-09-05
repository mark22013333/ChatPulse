#!/usr/bin/env bash
set -e

# 執行 MCP 安裝精靈（檢查環境／憑證、跑 OAuth 授權、輸出 MCP 設定片段）
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=./_venv.sh
source "$SCRIPT_DIR/_venv.sh"

ensure_venv
exec "$VENV_PYTHON" "$PROJECT_DIR/mcp_app/setup_wizard.py"
