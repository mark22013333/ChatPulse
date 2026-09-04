import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from . import config as cfg

class GoogleChatClient:
    def __init__(self):
        self.service = self._get_service()

    def _get_service(self):
        creds = None
        if os.path.exists(cfg.TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(cfg.TOKEN_FILE, cfg.CHAT_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(cfg.CLIENT_SECRET_FILE, cfg.CHAT_SCOPES)
                creds = flow.run_local_server(port=0)
            with open(cfg.TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
        return build('chat', 'v1', credentials=creds)

    def list_spaces(self):
        """列出所有空間"""
        res = self.service.spaces().list(pageSize=100).execute()
        return res.get('spaces', [])

    def find_space_by_name(self, name_keyword):
        """依名稱尋找空間"""
        spaces = self.list_spaces()
        for s in spaces:
            if name_keyword.lower() in s.get('displayName', '').lower():
                return s
        return None

    def fetch_recent_messages(self, space_id, limit=30):
        """
        分頁抓取指定空間的最近訊息，支援自訂筆數 (例如 100 筆)
        回傳依時間由舊到新的訊息列表
        """
        all_messages = []
        page_token = None
        
        while len(all_messages) < limit:
            fetch_count = min(100, limit - len(all_messages))
            req = self.service.spaces().messages().list(
                parent=space_id,
                pageSize=fetch_count,
                pageToken=page_token
            )
            res = req.execute()
            msgs = res.get('messages', [])
            if not msgs:
                break
            all_messages.extend(msgs)
            page_token = res.get('nextPageToken')
            if not page_token:
                break

        # Google 回傳通常是由新到舊，轉成由舊到新方便閱讀脈絡
        all_messages.reverse()
        return all_messages

    def send_message(self, space_id, text):
        """發送文字訊息到指定空間"""
        return self.service.spaces().messages().create(
            parent=space_id,
            body={'text': text}
        ).execute()
