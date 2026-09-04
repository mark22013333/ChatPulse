# Google Chat 智慧摘要與運維工具

本工具支援在終端機或透過 AI 輔助工具（如 Claude Code, Antigravity）直接執行對話抓取、Gemini 3.6 深度分析與自動推播。

## 快速使用

### 1. 基本指令 (Shell Script)
```bash
# 摘要指定群組 100 筆對話並推播回群組
/Users/cheng/google-chat-bot/scripts/run_summary.sh "0.暫存" 100

# 摘要 BU2-PG 群組 50 筆對話
/Users/cheng/google-chat-bot/scripts/run_summary.sh "1.BU2-PG" 50
```

### 2. 僅在終端查看，不推播回群組 (--no-post)
```bash
uv run --with google-api-python-client --with google-auth-oauthlib --with google-auth-httplib2 --with requests \
  python3 /Users/cheng/google-chat-bot/src/modules/summarizer.py --space "1.BU2-PG" --count 100 --no-post
```

## 在 Claude Code 中使用

在 Claude Code 的對話框中，您可以直接下達：
- `請幫我執行 /Users/cheng/google-chat-bot/scripts/run_summary.sh "1.BU2-PG" 100`
- 或者 `讀取 1.BU2-PG 最近 50 則對話並摘要`，Claude Code 便會自動調用本腳本。
