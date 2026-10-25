# ADO 唯讀 client（離線階段）

此模組僅提供三種固定 GET：指定工單、revisions、comments。尚未接入 SourceBroker、儀表板或正式 ADO。執行 `scripts/probe_ado_client.py` 只會啟動本機 TLS 假服務，使用程式內的假 PAT；不接受正式網址或 token 參數。

## 入口契約

呼叫端必須明確提供 `AdoConnection(collection_url, project)`、`AdoApiProfile(work_items, revisions, comments)` 與 PAT。client 不讀取環境變數、`config/` 或 `data/`。`collection_url` 必須是已確認的 HTTPS collection 入口，明確連接埠只允許與 `expected_port` 一致；若尚未確認 Server 版本、三支 API 版本、TLS 憑證鏈及 PAT 身分，呼叫端不應建立 client。這一版不推測 Azure DevOps Server 與雲端 API 的相容性。

`AdoClient` 以固定路徑自行組成請求。它不接受任意 URL、WIQL、關聯 URL 或跨 project 識別，也不跟隨 redirect、comments `nextPage`。`relations` 缺欄位時回 `None`，明確空陣列時回空 tuple；兩者意思不同。comments 的官方範例使用 `commentId`，定義使用 `id`，解析器接受任一欄位；兩欄同時出現但不相等時拒絕回應。

## 完整性與錯誤

revisions 使用 `$top`/`$skip`，以每頁 `count` 與 `value` 長度核對，短頁才算結束；滿頁會再查下一頁。comments 只用 `continuationToken` 取下一頁，直到明確 `null` 才算完整。重複 token、缺少 token、頁數／筆數／位元組上限或未完成的分頁都拋 `partial`，不回傳貌似完整的清單。JSON 重複欄位、NaN 與 Infinity 均回格式錯誤。每次讀取受 connect timeout、read timeout 與總 deadline 限制；`read_item_bundle` 三個端點共用同一預算。

`AdoReadError.reason` 可為 `auth_required`、`forbidden`、`not_found`、`timeout`、`invalid_response`、`unavailable` 或 `partial`。404 保留「不存在或沒有檢視權限」的歧義。例外訊息、`detail` 與 `repr` 只包含固定文字、端點代號及狀態碼，不包含 PAT、網址、標頭或回應本文。`requests.Session.trust_env=False`，TLS 驗證只能使用預設信任鏈或明確 CA 檔，無關閉驗證的選項。

## 重跑驗證

```bash
.venv/bin/python -m unittest tests.unit.test_ado_client
.venv/bin/python scripts/probe_ado_client.py
```

正式串接前須另行確認可信任 HTTPS 入口、Server/API profile、PAT 權限與資料揭露範圍；此文件不宣稱本機探針可證明正式服務相容。
