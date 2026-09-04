import requests
import json
from . import config as cfg

class GeminiClient:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or cfg.GEMINI_API_KEY
        if not self.api_key:
            raise RuntimeError(
                "缺少 Gemini API key。請先設定環境變數再啟動：\n"
                "  export GOOGLE_API_KEY='你的 Gemini API key'"
            )
        self.model = model or cfg.GEMINI_MODEL
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

    def summarize_discussion(self, space_name, conversation_text, message_count):
        """
        將對話紀錄傳送給 Gemini 進行結構化提煉
        """
        system_instruction = (
            "你是一個專業的企業專案與團隊溝通專家。你的任務是閱讀 Google Chat 群組的對話紀錄，"
            "並產出一份條理分明、高資訊密度、且版面美觀的「對話智慧摘要與追蹤清單」。\n\n"
            "請嚴格依據以下結構輸出（使用 Google Chat 支援的 Markdown 語法，例如粗體使用 *文字*，程式碼用 `code`）：\n"
            "1. 標題：🤖 *【對話智慧摘要｜{空間名稱}】* (最近 {訊息數量} 則分析)\n"
            "2. 📌 *核心討論主題與脈絡*（分點說明討論的主要事情、關鍵技術名詞、爭點或進展）\n"
            "3. 🤝 *共識與關鍵決議*（如果大家有達成任何結論或定案，清楚條列；若無則說明尚在討論中）\n"
            "4. 🎯 *待辦事項與追蹤 (Action Items)*（明確標示：`• [負責人或組別] 具體執行任務`）\n\n"
            "注意：請過濾掉純粹打招呼、貼圖等無實質意義的閒聊，聚焦於工作進度、技術方案、問題排查與需求指派。"
        )

        prompt = f"""
目標聊天室：{space_name}
分析對話則數：{message_count} 則

【以下為原始對話紀錄】
{conversation_text}

請開始進行結構化摘要：
"""

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_instruction}\n\n{prompt}"}]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 2048
            }
        }

        resp = requests.post(self.endpoint, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API 請求失敗 ({resp.status_code}): {resp.text}")

        data = resp.json()
        try:
            summary = data["candidates"][0]["content"]["parts"][0]["text"]
            return summary
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"解析 Gemini 回傳失敗: {e}, Response: {data}")
