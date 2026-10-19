# Draft Reply 離線 judgment 評估

這份 corpus 全為手寫合成資料，不含真實對話、內部網址或憑證。它檢查外部 candidate 對列明陳述的結構化判斷。scorer 不呼叫模型、不讀 API key、不解讀自由文字，也不檢查完整 Draft Reply 是否另外新增了未列明陳述。

## 執行

從儲存庫根目錄執行。

```bash
.venv/bin/python scripts/evaluate_reply_cases.py
.venv/bin/python scripts/evaluate_reply_cases.py lint --corpus tests/fixtures/reply_cases.jsonl
.venv/bin/python scripts/evaluate_reply_cases.py score --corpus tests/fixtures/reply_cases.jsonl --candidate tests/fixtures/reply_candidates.good.jsonl --output /tmp/reply-evaluation.json
.venv/bin/python -m unittest discover -s tests/unit -p test_evaluate_reply_cases.py
```

預設只做 corpus lint，不回分數。`score` 必須提供與 corpus 不同的完整 candidate 檔案。工具沒有由 gold 產生 candidate 的模式。`reply_candidates.good.jsonl` 是獨立手寫的 scorer 正對照；其通過只證明 scorer 能比較這些 judgment，不能宣稱模型正確率。

stdout 是 JSON。`--output` 可額外寫出同一份報表，且不得覆寫 corpus 或 candidate。離開碼 `0` 表示 lint 通過或所有 judgment 符合要求；`1` 表示合法 candidate 有評分失敗；`2` 表示參數、檔案、JSON 或 schema 不合法，沒有評分。

## 五組正反對照

| 組別 | 反例 | 可回答的正對照 | 關鍵差異 |
| --- | --- | --- | --- |
| 工單 Done | `done_not_deployed` | `done_and_verified` | Done 與開發驗收不足以證明正式修復；正式部署及驗證才足夠。 |
| 合併與部署 | `merged_not_deployed` | `merged_and_deployed` | 合併紀錄與明確尚未部署，不能回答已部署。 |
| UAT 與正式 | `uat_not_production` | `production_timeout` | 可定位的 UAT 程式碼不能代表正式環境。 |
| 舊部署與回滾 | `deployment_rolled_back` | `deployment_still_current` | 歷史 v2 部署已被回滾取代；新的執行中檢查才可代表目前版本。 |
| 承諾強度 | `planned_not_guaranteed` | `explicit_guarantee` | 日期不變也可能改壞事實；「預計」不能提升成「保證」。明確承諾只能支持承諾本身，不能支持已部署。 |

## Corpus schema

每列必須是一個物件，恰好包含 `case_id`、`question`、`scope`、`evidence`、`claims`、`gold`。每一層都拒絕未知或缺漏欄位。

- `scope` 包含 `project`、`environment`、`as_of`。環境只允許 `production|uat|dev`，時間必須含時區。
- `evidence` 是非空陣列。每項包含 `id`、`locator`、`lines`、`text`、`project`、`environment`、`event_at`。`lines` 是含起訖的 `[start, end]` 正整數行號，範圍長度須等於 `text` 行數。locator 使用虛構的穩定定位字串。
- `claims` 是非空陣列，每項包含 `id` 與 `text`。潤稿反例直接列出強度提升後的完整陳述。
- `gold` 只包含 `claims`，每項包含 `claim_id`、`relation`、`currency`、`scope`、`allowed_decisions`、`required`、`citation_spans`。其 ID 必須恰好對應所有 claim。`required` 必須為布林值，必答項不得允許 `omit`。gold 不得允許不安全的 `assert`。`citation_spans` 是支持人工 judgment 的必要證據範圍，格式為 `[{"evidence_id":"e1","lines":[2,2]}]`。多個 span 必須全部被引用覆蓋，每個額外引用也須包含至少一個必要 span。時間、scope 或語意判斷需要不同原文時，分別標註必要 span。此版本不處理可替代來源。

case、claim、evidence 的 ID 不得重複，且只使用英數字、底線或連字號。判斷值域如下。

| 欄位 | 允許值 |
| --- | --- |
| `relation` | `supported`、`contradicted`、`insufficient` |
| `currency` | `current`、`superseded`、`unknown` |
| `scope` | `match`、`mismatch`、`unknown` |
| `decision` | `assert`、`qualify`、`omit` |

`relation` 比較陳述與提供的證據，`currency` 表示證據是否足以代表提問時點，`scope` 比較專案與環境。三者分開標註，不能把環境不符藏在過時標籤中。外部 producer 必須獨立判斷這些語意，scorer 不自行推論。

## Candidate schema

每列恰好包含 `case_id` 與 `claims`。每個 claim 恰好包含 `claim_id`、`decision`、`relation`、`currency`、`scope`、`references`。

```json
{"case_id":"done_not_deployed","claims":[{"claim_id":"c1","decision":"qualify","relation":"insufficient","currency":"unknown","scope":"mismatch","references":[{"evidence_id":"e1","lines":[1,2]}]}]}
```

candidate 必須逐 case、逐 claim 完整提交。`omit` 仍須列出所有判斷欄位，但可使用空的 `references`。`assert` 或 `qualify` 沒有 reference 會產生定位失敗。

reference 恰好包含 `evidence_id` 與 `lines`。未知 case、claim、evidence ID、重複 ID 或 reference、非法值、倒序或非正整數行號，都屬輸入錯誤，回 `2`。已知 evidence 的合法格式行號若超出可定位範圍，則是評分失敗，回 `1`。拒絕重複 JSON key、空白列、NaN 等非標準 JSON。candidate 不接受 `gold`、原文 `text`、自由文字理由或其他欄位，因此不能附 corpus evidence 原文副本。

## 報表解讀

每例每 claim 的 `scores` 分別列出 `relation`、`currency`、`scope`、`decision`、`reference_validity`、`citation_support`、`unsafe_assertion`、`over_refusal`。所有維度的 `true` 都表示通過。後兩者為 `true` 表示沒有發生該問題。頂層 `scores` 為各維度通過數與總數。

- `relation_mismatch`、`currency_mismatch`、`scope_mismatch` 分別指出 candidate 與 gold 不符。
- `decision_mismatch` 表示不在 gold 允許決策內。
- `invalid_reference` 表示缺少必要引用，或引用超出該 evidence 範圍。
- `unsupported_citation` 表示引用未覆蓋所有必要 span，或額外引用不包含任何必要 span。只有省略陳述且沒有引用時，此項免檢查。
- `unsafe_assertion` 表示 assert 的陳述在 gold 中不是同時 `supported`、`current`、`match`。此判斷依據 gold，因此 candidate 不能靠自稱 supported 規避。
- `over_refusal` 表示將 gold `required:true` 的可回答項目 omit。

行號可定位與語意支持分開計算。例如 `done_and_verified` 只引用第 1 行「工單狀態為 Done」時，`reference_validity` 通過，但 `citation_support` 失敗。`reference_validity:true` 只證明行號落在 corpus 片段內，不證明內容支持結論。scorer 也不驗證文字改寫品質或新陳述；如要評估模型，須另外建立獨立 producer 與人工標註流程。
