import os
import sys
import time
import json
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.chat_client import GoogleChatClient
from core.gemini_client import GeminiClient
from core import config as cfg
import requests

app = FastAPI(title="ChatPulse - Google Chat Dashboard API", version="1.0.0")

# 允許 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_client = GoogleChatClient()
gemini_client = GeminiClient()

# 5 分鐘 Spaces 記憶體快取
_SPACES_CACHE = {
    "timestamp": 0,
    "data": []
}

class PublishRequest(BaseModel):
    space_id: str
    text: str

class SummarizeRequest(BaseModel):
    space_id: str
    limit: int = 100
    style: str = "general" # general, technical, action_only

@app.get("/api/v1/spaces")
def get_spaces(search: Optional[str] = None, refresh: bool = False):
    """
    取得群組/空間清單，內建 5 分鐘快取
    """
    now = time.time()
    if refresh or not _SPACES_CACHE["data"] or (now - _SPACES_CACHE["timestamp"] > 300):
        try:
            raw_spaces = chat_client.list_spaces()
            formatted = []
            for s in raw_spaces:
                formatted.append({
                    "id": s.get("name"),
                    "displayName": s.get("displayName") or "（私訊/未命名空間）",
                    "type": s.get("spaceType", "UNKNOWN"),
                })
            _SPACES_CACHE["data"] = formatted
            _SPACES_CACHE["timestamp"] = now
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"無法取得 Google Chat 空間清單: {e}")

    results = _SPACES_CACHE["data"]
    if search:
        s_lower = search.lower()
        results = [s for s in results if s_lower in s["displayName"].lower()]

    return {
        "count": len(results),
        "cached": not refresh,
        "spaces": results
    }

@app.get("/api/v1/spaces/{space_id:path}/messages")
def get_space_messages(space_id: str, limit: int = Query(default=50, ge=1, le=500)):
    """
    抓取指定群組的歷史對話
    """
    try:
        messages = chat_client.fetch_recent_messages(space_id, limit=limit)
        formatted = []
        for m in messages:
            sender = m.get("sender", {}).get("displayName", "未知成員")
            create_time = m.get("createTime", "")[:19].replace("T", " ")
            text = m.get("text", "").strip()
            if text:
                formatted.append({
                    "sender": sender,
                    "time": create_time,
                    "text": text
                })
        return {
            "space_id": space_id,
            "count": len(formatted),
            "messages": formatted
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"抓取訊息失敗: {e}")

@app.post("/api/v1/spaces/{space_id:path}/summarize/stream")
def stream_summarize(space_id: str, req: SummarizeRequest):
    """
    SSE 串流打字機摘要端點 (Server-Sent Events)
    """
    # 1. 取得空間資訊
    spaces = _SPACES_CACHE["data"]
    space_name = space_id
    for s in spaces:
        if s["id"] == space_id:
            space_name = s["displayName"]
            break

    # 2. 抓取訊息
    messages = chat_client.fetch_recent_messages(space_id, limit=req.limit)
    if not messages:
        def empty_stream():
            yield "event: error\ndata: 該空間查無足夠對話訊息供摘要。\n\n"
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    # 3. 組合對話文本
    formatted_lines = []
    for m in messages:
        sender = m.get("sender", {}).get("displayName", "未知成員")
        create_time = m.get("createTime", "")[:16].replace("T", " ")
        text = m.get("text", "").strip()
        if text:
            formatted_lines.append(f"[{create_time}] {sender}: {text}")
    conversation_text = "\n".join(formatted_lines)

    # 4. 準備串流呼叫 Gemini (streamGenerateContent)
    def event_generator():
        prompt = f"""
你是一個頂級企業專案與工程研發主管。請分析以下來自「{space_name}」的最新 {len(messages)} 則 Google Chat 對話，並產生結構化摘要。

【輸出規範】
請嚴格使用清晰的 Markdown 格式：
### 📌 核心討論主題與脈絡
（分點提煉關鍵事情、技術名詞、排障過程或專案進展）

### 🤝 共識與重要決議
（條列會中拍板定案的事情；若無請說明「尚在討論中」）

### 🎯 待辦事項與追蹤 (Action Items)
（格式務必精確：`• [負責人或組別] 具體任務內容`，方便系統自動提取）

【對話紀錄】
{conversation_text}
"""
        stream_url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.GEMINI_MODEL}:streamGenerateContent?key={cfg.GEMINI_API_KEY}&alt=sse"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048
            }
        }
        
        try:
            with requests.post(stream_url, json=payload, headers={"Content-Type": "application/json"}, stream=True) as r:
                for line in r.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8')
                        if decoded_line.startswith("data: "):
                            raw_json = decoded_line[6:]
                            try:
                                chunk = json.loads(raw_json)
                                text_chunk = chunk.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                if text_chunk:
                                    yield f"data: {json.dumps({'chunk': text_chunk}, ensure_ascii=False)}\n\n"
                            except Exception:
                                pass
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"event: error\ndata: {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/api/v1/spaces/publish")
def publish_message(req: PublishRequest):
    """
    推播訊息回 Google Chat 群組
    """
    try:
        res = chat_client.send_message(req.space_id, req.text)
        return {
            "status": "success",
            "message_id": res.get("name"),
            "createTime": res.get("createTime")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"推播失敗: {e}")

# 靜態首頁託管
WEB_DIR = os.path.join(BASE_DIR, "dashboard", "frontend")
if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

@app.get("/")
def serve_index():
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "ChatPulse API is running. Web UI not found in /web."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
