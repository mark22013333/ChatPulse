#!/usr/bin/env bash
# 共用函式：確保專案虛擬環境存在並可用。
#
# 這個檔案是給其他 script `source` 用的，不要直接執行。
#
# 優先順序（SPECIFICATION.md 十三節「乾淨環境可重建」）：
#   1. 已有 .venv → 直接使用（版本由 requirements.txt 鎖定）
#   2. 沒有      → 用 uv 以 Python 3.12 建立，再安裝 requirements.txt 的鎖定版依賴
#
# 用法：
#   SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
#   source "$SCRIPT_DIR/_venv.sh"
#   ensure_venv
#   "$VENV_PYTHON" some_script.py

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
VENV_DIR="$PROJECT_DIR/.venv"
VENV_BIN="$VENV_DIR/bin"
VENV_PYTHON="$VENV_BIN/python"
REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"

find_uv() {
    local uv_bin
    uv_bin="$(command -v uv || true)"
    if [ -z "$uv_bin" ] && [ -x "$HOME/.local/bin/uv" ]; then
        uv_bin="$HOME/.local/bin/uv"
    fi
    if [ -z "$uv_bin" ]; then
        echo "⚠️ 未偵測到 uv 套件管理器，正在為您安裝..." >&2
        curl -LsSf https://astral.sh/uv/install.sh | sh >&2
        uv_bin="$HOME/.local/bin/uv"
    fi
    echo "$uv_bin"
}

ensure_venv() {
    if [ -x "$VENV_PYTHON" ]; then
        echo "✅ 使用專案虛擬環境：$VENV_DIR"
        return 0
    fi

    # ${} 不可省略：macOS 內建的 bash 3.2 會把緊接其後的全形「，」位元組
    # 當成變數名的一部分，於是整個路徑消失、只留半截亂碼（實測 2026-09-07）。
    # 這行原本印不出 .venv 的位置——正是使用者最需要看到的那個資訊。
    echo "⚠️ 找不到虛擬環境 ${VENV_DIR}，正在建立（Python 3.12）..."
    local uv_bin
    uv_bin="$(find_uv)"

    "$uv_bin" venv --python 3.12 "$VENV_DIR"
    "$uv_bin" pip install --python "$VENV_PYTHON" -r "$REQUIREMENTS_FILE"
    echo "✅ 虛擬環境建立完成：$VENV_DIR"
}
