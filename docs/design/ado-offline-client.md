# ADO 唯讀 client（離線階段）

此模組僅提供指定工單、revisions、comments 三種固定 GET。尚未接入 SourceBroker、儀表板或正式 ADO。`scripts/probe_ado_client.py` 只會啟動本機 TLS 假服務，使用程式內的假 PAT，不接受正式網址或 token 參數。

## 入口契約

呼叫端明確提供 `AdoConnection(collection_url, project)`、`AdoApiProfile(work_items, revisions, comments)` 與 PAT。client 不讀取環境變數、`config/` 或 `data/`。`collection_url` 必須是已確認的 HTTPS collection 入口，明確連接埠只允許與 `expected_port` 一致。正式串接仍須確認 Server 版本、三支 API 版本、TLS 憑證鏈及 PAT 身分。這一版不推測 Azure DevOps Server 與雲端 API 的相容性。

`get_work_item`、`list_revisions`、`list_comments` 與 `read_item_bundle` 均為原生 async 方法，呼叫端以 `await client.read_item_bundle(42)` 取得領域結果。每次公開操作建立並關閉自己的 HTTPX AsyncClient，bundle 內共用同一個 client，不跨事件迴圈共用 AsyncClient。沒有同步 wrapper，CLI probe 僅在最外層呼叫一次 `asyncio.run`。測試可注入 `httpx.AsyncBaseTransport` 或 `httpx.MockTransport`，操作結束會關閉注入的 transport。

`AdoClient` 以固定路徑自行組成請求。它不接受任意 URL、WIQL、關聯 URL 或跨 project 識別，也不跟隨 redirect、comments `nextPage`。`relations` 缺欄位時回 `None`，明確空陣列時回空 tuple，兩者意思不同。comments 的官方範例使用 `commentId`，定義使用 `id`。解析器接受任一欄位，兩欄同時出現但不相等時拒絕回應。

## 完整性與期限

revisions 使用 `$top` 與 `$skip`，以每頁 `count` 與 `value` 長度核對，短頁才算結束，滿頁會再查下一頁。comments 只用 `continuationToken` 取下一頁，直到明確 `null` 才算完整。重複 token、缺少 token、頁數、筆數或位元組上限，以及未完成的分頁都拋 `partial`，不回傳貌似完整的清單。JSON 重複欄位、NaN 與 Infinity 均回格式錯誤。

每個公開操作以 `loop.time()` 建立一次絕對 deadline。`asyncio.timeout_at` 涵蓋 connect、TLS 握手、HTTP headers、chunk metadata、本文與所有分頁。`read_item_bundle` 的三個端點共用同一個期限與位元組預算。HTTPX 的 connect、read、pool timeout 提供額外階段上限，不會重設總期限。client 要求 identity 編碼，以 `aiter_raw` 讀取本文，擴張緩衝區前檢查位元組上限。伺服器回傳壓縮內容時明確拒絕。JSON 與領域解析完成後仍檢查期限，過期不回完整結果。

這是協作式 I/O 期限。同步 JSON 解析、事件迴圈遭阻塞、OS resolver 與應用程式 runner 關閉不保證精準硬中止。OS DNS 可能在操作取消後繼續由事件迴圈標準 executor 執行，應用程式的 `asyncio.run` 結束時可能等待它。client 本身沒有新增 worker thread，也不宣稱零 resolver 背景工作。

## 清理與錯誤

依賴鎖定 `httpx==0.28.1` 與 `httpcore==1.0.9`。這組版本在 TLS 握手尚未交接時取消，單靠 pool 清理可能讓連線留到 GC。client 使用公開 trace extension，僅保留 `connection.connect_tcp.complete` 的 `return_value`，在 `connection.start_tls.complete` 後交回 HTTPX。若握手未完成便離開，`finally` 直接 `aclose` 該 stream，不取得私有 socket、backend 或自行解析 HTTP。trace 不記錄 URL、標頭、本文或原始錯誤。升級依賴時須重跑 GC 前 EOF 測試。

`AdoReadError.reason` 可為 `auth_required`、`forbidden`、`not_found`、`timeout`、`invalid_response`、`unavailable` 或 `partial`。404 保留「不存在或沒有檢視權限」的歧義。`TimeoutError` 與 HTTPX timeout 轉為 `timeout`，其他 HTTPX 錯誤轉為 `unavailable`，隱藏原始例外。外部 `CancelledError` 經清理後原樣傳出，不重試。未知清理錯誤會傳出，不能視為成功。

領域例外訊息、`detail` 與 `repr` 只包含固定文字、端點代號及狀態碼，不包含 PAT、網址、標頭或回應本文。HTTPX 設為 `trust_env=False` 與 `follow_redirects=False`。環境 proxy 與 CA 變數不覆寫設定，TLS 由 `SSLContext(PROTOCOL_TLS_CLIENT)` 建立，只載入 `certifi==2026.7.22` 的信任鏈或明確 CA 檔，沒有關閉驗證的選項。不使用會讀取 `SSLKEYLOGFILE` 的 `create_default_context`，不產生 TLS session key 檔。

`core/private_http.py` 在載入時一次安裝 LogRecord factory，鏈結原有 factory。ADO 操作以 ContextVar 標記範圍，HTTPX、HTTPCore 與其所有子 logger 的訊息、參數、例外及 stack 內容在送給 handler 前改成固定安全內容。其他 task、其他 logger、level 與 handler 不變，離開或取消操作時還原範圍。這避免 DEBUG 回應標頭與 INFO 請求 URL 洩漏；父 logger filter 無法保護子 logger，因此不採用。後續私密 HTTP 邊界可共用 `private_http_client(deadline, ca_file=...)`。這個 async context manager 擁有期限、TLS 信任鏈、request trace 與取消清理；領域 client 仍負責固定 URL、狀態分類、本文與 schema，不在共用層轉譯領域例外。每個 context 僅支援依序請求，操作結束即關閉 client。

## 重跑驗證

```bash
.venv/bin/python -m unittest tests.unit.test_ado_client tests.unit.test_ado_tls tests.unit.test_http_privacy
.venv/bin/python scripts/probe_ado_client.py
.venv/bin/python -m unittest discover -s tests/unit
```

TLS 測試使用本機假 CA 與假資料，涵蓋慢 header、chunk extension、trailer、本文滴流、idle timeout、跨頁與 bundle 共享期限、TLS 握手 timeout、外部取消與 GC 前 EOF。它也驗證可信與不可信 CA、proxy、固定 GET、redirect、完整性與秘密遮罩。本機測試通過不代表正式 ADO 相容。
