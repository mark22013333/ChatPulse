#!/bin/bash
# 方便使用的快捷腳本
# 用法範例:
#   ./scripts/run_summary.sh "0.暫存" 100
#   ./scripts/run_summary.sh "1.BU2-PG" 50

SPACE=${1:-"0.暫存"}
COUNT=${2:-30}

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"

uv run --with google-api-python-client --with google-auth-oauthlib --with google-auth-httplib2 --with requests \
  python3 "$DIR/mcp_app/summarizer.py" --space "$SPACE" --count "$COUNT"
