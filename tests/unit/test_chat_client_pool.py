"""`GoogleChatClient` 的 HTTP 連線池大小。

同一位 Viewer 的 client 是跨請求快取共用的，會被 Mention 輪詢、列表抓內容、
附件下載三個執行緒池同時使用。連線池比並行數小的時候，結果仍然正確，
只會一直印「Connection pool is full」並白做 TLS 握手——沒有任何功能測試
抓得到這種退化，所以由這裡守住「連線池 ≥ 最壞並行數」。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import sys
import unittest

from google.oauth2.credentials import Credentials

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core import config as cfg  # noqa: E402
from core.chat_client import API_BASE, MEDIA_BASE, GoogleChatClient  # noqa: E402

# dashboard/api/server.py 列表即時抓訊息內容用的 min(8, …)
LIST_CONTENT_FETCH_WORKERS = 8


def _pool_maxsize(client: GoogleChatClient, url: str) -> int:
    return client._session.get_adapter(url)._pool_maxsize


class ChatClientPoolTest(unittest.TestCase):
    def setUp(self):
        # 假憑證：建構時不打網路，只看 session 的設定
        self.client = GoogleChatClient(Credentials(token="fake-token"), interactive=False)

    def test_連線池涵蓋三個執行緒池的最壞並行數(self):
        worst = cfg.MENTION_POLL_WORKERS + LIST_CONTENT_FETCH_WORKERS + cfg.IMAGE_DOWNLOAD_WORKERS
        self.assertGreaterEqual(_pool_maxsize(self.client, API_BASE + "/spaces"), worst)

    def test_附件的media端點也用同一個放大的連線池(self):
        self.assertEqual(
            _pool_maxsize(self.client, MEDIA_BASE + "/v1/media/x"),
            cfg.CHAT_HTTP_POOL_MAXSIZE,
        )

    def test_正對照_未掛載的requests預設只有10(self):
        # 證明上面讀到的數字真的來自我們的掛載，而不是 requests 的預設值剛好夠大
        import requests

        self.assertEqual(requests.Session().get_adapter(API_BASE)._pool_maxsize, 10)


if __name__ == "__main__":
    unittest.main()
