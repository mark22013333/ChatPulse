# SIWC 協定首切片

`core/siwc_protocol.py` 將 SIWC callback 交換成經驗證的內部 `VerifiedGrant`。本切片沒有 Viewer 資料庫綁定、callback listener、HTTP 路由、瀏覽器啟動、refresh、撤銷或 runtime 接線。取得 grant 不會啟用連線，也不表示推論已可使用。

## 呼叫端與資料擁有權

`OpenAIProtocol.prepare(host_id, redirect_uri, registration=None)` 回傳 `AuthorizationStart` 與 `PendingAuthorization`。呼叫端先綁定 loopback listener，再提供精確的 `http://127.0.0.1:<port>/auth/callback`。協定不替呼叫端開啟 listener 或產生持久化 host ID。

`AuthorizationStart` 包含 attempt ID、到期時間及一次性導向 URL。URL 只供開啟授權頁，不得寫入日誌或其他狀態 DTO。`PendingAuthorization` 包含 state digest、nonce、PKCE verifier、精確 callback URI、原 client ID、原 verified identity 與五分鐘期限。它只能保留在伺服器端。`repr=False` 只防止意外顯示，不防止 `dataclasses.asdict` 或其他序列化器輸出秘密。

新註冊使用 `dynamic_agent_client` 與 `agent_name_hint=ChatPulse`。`Registration(client_id, identity=None)` 表示重用已核發的 client ID，包含曾完成註冊但授權碼交換失敗的情況。提供已驗證 identity 時，新的 issuer、subject 與 client ID 必須相同。email 只作顯示，不參與相等判斷。

`await OpenAIProtocol.complete(pending, raw_query, claim_callback=...)` 只有一個完整交換入口。它驗證 query 格式、state、TTL 與 client ID，再等待必要的 claim hook，最後交換授權碼及驗證 OIDC。呼叫端不能省略 hook。

claim hook 接受 `CallbackAccepted(attempt_id, state_digest, issued_client_id)` 或 `CallbackDenied(attempt_id, state_digest)`。hook 不取得授權碼、token、nonce 或 verifier。正常回傳代表 claim 成功；一般例外轉為固定的 `CLAIM_REJECTED`，取消則保留 `asyncio.CancelledError`。合法 `access_denied` 先消耗 claim，再回傳 `ACCESS_DENIED`，不交換授權碼。

未來 repository 必須在同一筆交易中確認原 Viewer、同一 live session、attempt 期限與 CAS，消耗 state 並 checkpoint issued client ID。協定不另建重播 registry。測試的 `RecordingClaim` 只模擬一次性消耗，不驗證 Viewer/session，不構成持久化安全性證據。claim 成功後，網路失敗或呼叫端取消也不能重播原授權碼。

## OIDC 與授權界線

身分採 `(issuer, subject, client_id)`。`VerifiedGrant` 保存已驗證身分、token response 的 scopes、收到回應的時間、ID token 與可選的 `AccessCredentials`。identity-only 回應可完全省略 access credentials；`offline_access` 必須搭配 access token 與 refresh token。宣稱 `chatgpt.tokens.use.direct` 卻沒有 access credentials 的回應會失敗。

`has_plan_grant` 只檢查 token response 的 `chatgpt.tokens.use.direct` scope。callback 的 scope、ID token 的 email、模型清單與 connected 標示都不提供 plan 授權，也不能當作模型資格或成功推論的證據。首次交換必須明確提供 scope、`openid` 及 ID token。access token 必須符合 RFC 6750 的 Bearer token 語法。有 access token 時，回應必須提供 Bearer type 與正數 `expires_in`，上限一年；此上限是本機輸入限制，不是服務端 token 壽命宣稱。

`earliest_refresh_at` 暫以長度最多 256 的非空字串或有限數值保留，拒絕布林值。本切片不執行 refresh，不解析這個提示，也不把它當作可推論資格。官方 SDK 的排程程式將數字視為 Unix 秒、字串交給日期解析；後續 refresh 切片再依該語意處理。此階段保留原值，時間格式轉換由後續 refresh 流程處理。

JWT 由 PyJWT 與 cryptography 驗證，演算法固定 RS256。必填 `iss`、`aud`、`exp`、`iat`、`sub` 與 `nonce`；存在 `nbf` 時也驗證。時間容忍為五秒。`sub` 不可空白。多個 audience 必須有相符的 `azp`，任何提供的 `azp` 都必須等於 issued client ID。標頭中的 `jku`、`x5u`、`crit` 與 `b64` 一律拒絕。

公開設定固定如下。discovery 回應必須與這些值完全相符。

| 欄位 | 固定值 |
| --- | --- |
| issuer | `https://auth.openai.com` |
| authorization endpoint | `https://auth.openai.com/api/accounts/authorize` |
| token endpoint | `https://auth.openai.com/api/accounts/oauth/token` |
| discovery | `https://auth.openai.com/.well-known/openid-configuration` |
| JWKS | `https://auth.openai.com/.well-known/jwks.json` |
| revocation metadata | `https://auth.openai.com/api/accounts/oauth/revoke` |
| resource | `https://api.openai.com/v1` |
| signing algorithms | `["RS256"]` |

JWKS 必須是 1 至 32 把具有唯一 kid 的 RSA、RS256、sig 公開金鑰，選中的 RSA key 為 2048 至 8192 bits。未知 kid 最多再讀取一次固定 JWKS。沒有跨操作快取。JSON 解析拒絕重複欄位、非有限數字及非物件頂層；JWT header 與 payload 也先經相同嚴格解析，再交 PyJWT 驗簽與檢查 claims。

## HTTP 與錯誤

`core/private_http.py` 擁有私密 HTTP 的 TLS、日誌遮罩、取消清理及總期限。協定的一次 `complete` 預設共用二十秒 deadline，涵蓋 claim、授權碼交換、discovery 與最多兩次 JWKS。所有 HTTP 共用一個 client 與累計 262144 bytes 回應預算，每個回應最多 131072 bytes。協定不把單次 read timeout 當作總期限。它拒絕 redirect 與壓縮回應，使用原始 body 計數，避免自動解壓縮繞過大小限制。

`transport`、`ca_file` 與 `timeout_seconds` 僅供受信任的程式組裝和本機測試，不能接受 API body、query 或 token header 的設定。正式端點沒有 override。測試 transport 只把固定 OpenAI URL 對應到本機假 TLS server，仍驗證測試憑證。

`SiwcProtocolError.code` 區分 callback 格式、到期、拒絕授權、claim 失敗、失效授權碼、token 格式、身分不符、metadata、服務不可用與總期限。錯誤不附外部本文、SQL、完整 URL 或 token。未知的儲存失敗也不傳出原始例外內容。

## 驗證範圍

`tests/fixtures/siwc_oidc.py` 執行真的 loopback TLS server，動態產生測試憑證與 RSA token。`tests/unit/test_siwc_protocol.py` 驗證正常交換的實際 HTTP form、claim 順序、重播與取消的零次或一次交換、identity-only、scope 來源、JWT 負向案例、metadata 與 JWKS、body 預算、整體期限，以及 repr 與 DEBUG 日誌的秘密遮罩。

這些測試沒有連到正式 OpenAI 端點，也沒有驗證真人登入、Viewer 持久化綁定、啟用 CAS、帳號切換、旋轉 token、登出或推論。後續入口仍須驗證有效 Google Viewer session、Origin、CSRF 與 owner 範圍，候選 grant 必須經原發起 session 明確啟用。

協定依據為已核對的官方 [SIWC 登入流程](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)、[帳號與工作階段](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)，以及本次已保存的官方 SDK OAuth 實作。2026-10-26 的唯讀公開 metadata 查核確認 RS256 與上述固定端點；本切片沒有重新進行正式登入驗證。
