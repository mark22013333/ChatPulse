"""ZPlanner（公司自研工時系統）的 REST client。

⚠️⚠️ **這支 API 的 HTTP 狀態碼恆為 200，成敗一律看 response body 的 code 欄位。**

    {"code": 403, "message": "權限不足", "data": null}   ← HTTP 狀態碼仍然是 200

這違反所有人的直覺，所以先講清楚會怎麼壞：任何靠 `resp.raise_for_status()`、
`if resp.ok`、`curl -f` 判斷成敗的程式碼，在這支 API 上會把**權限不足、token
失效、參數格式錯誤全部當成功處理**，然後拿著 `data: null` 往下跑。錯誤不會有
任何跡象，只會在更下游變成一個看不懂的 None。

因此這個模組唯一的對外保證是：**每個公開方法要嘛回傳成功的 data，要嘛拋出
ChatPulseError 的子類別**。呼叫端不需要、也不應該自己檢查任何狀態碼。

已知的 code（2026-09-11 對正式站實測）：

    200  成功
    400  hours/date/start_time 格式錯誤
    401  請先登入（token 無效或沒帶）
    403  權限不足
    404  找不到工時

其他反直覺的地方，每一條都在下面的實作處標了註解：

* `GET /api/issues/worklogable/` 的 `q` **必填**，空字串回 0 筆（而不是全部），
  而且 OpenAPI schema 裡完全沒有宣告這個參數。
* 分頁參數是 `per_page`，不是 `page_size`——後者無效且**靜默忽略**。
* 分頁容器的 key 在不同端點**不一致**：`items` 與 `results` 都有。
* `issues/mine` 與 `issues/worklogable` 是**不同集合**，互有增減，不可互相取代。

認證只吃 `Authorization: Bearer zp_<64 hex>` 一種帶法；另外 11 種（`X-API-Key`、
`Authorization: Token`、query param `?apikey=` 等）實測全部回 401，不要支援。

範圍：這一層只做 HTTP 與錯誤轉譯，**不做任何業務判斷**。要不要填工時、填幾
小時、算在哪個 issue，全部是上層的事。
"""

import csv
import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import config as cfg
from .errors import (
    ConfigurationError,
    InvalidParameter,
    ZPlannerApiError,
    ZPlannerUnavailable,
    ZPlannerWriteIndeterminate,
    classify_zplanner_error,
)

log = logging.getLogger("chatpulse.zplanner")

#: schema 的 securitySchemes 宣告 bearerFormat 為 zp_ 加 64 個十六進位字元。
_TOKEN_PATTERN = re.compile(r"zp_[0-9a-fA-F]{64}")

#: 容器裡「那一批資料」可能叫這些名字。**順序有意義**：先找專案 issue 用的
#: items，再找跨專案 issue 用的 results，最後是某 issue 的工時用的 worklogs。
#: 三個端點各用一種，不是筆誤。
#:
#: ⚠️ `worklogs` 是 2026-09-14 才補上的，補之前 `GET /api/issues/{id}/worklogs/`
#: 會讓 `_unwrap_page` 直接拋「找不到資料欄位」。那支回的是
#: `{actual_hours, worklogs}`——**沒有任何分頁欄位**，所以 total_pages 會是
#: None（一次回完），這是正確的結果，不是漏讀了分頁。
_PAGE_ITEM_KEYS = ("items", "results", "worklogs")

#: 總頁數的欄位。`total`（專案 issue）與 `count`（跨專案 issue）是總**筆數**
#: 不是頁數，所以不能拿來當迴圈終止條件——這裡只認 total_pages。
_TOTAL_PAGES_KEY = "total_pages"

#: `hours` 的合法形狀：最多三位整數、兩位小數，允許負數（ZPlanner 用負數做沖銷）。
#:
#: ⚠️ **比 schema 嚴格一點，而且是故意的。** OpenAPI 原文是
#: `^-?\d{0,3}(?:\.\d{0,2})?$`——`{0,3}` 與 `{0,2}` 的下界都是 0，所以
#: **空字串、`"-"`、`"."` 全都符合那個 pattern**。照抄的話 `hours=""` 會被
#: 放行送出去，換回一句分不出欄位的「格式錯誤」。前面的 `(?=.*\d)` 要求至少
#: 有一個數字，把這三種擋在送出之前。
_HOURS_PATTERN = re.compile(r"-?(?=.*\d)\d{0,3}(?:\.\d{0,2})?")

#: `date` 的形狀。只驗長相，真實性交給 ZPlanner。
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

#: 工時匯出 CSV 的中文欄名 → 我們用的鍵（2026-09-14 實測的欄位順序）。
#:
#: 正規化的理由：`工時(h)` 這種 key 會漏進每一個呼叫端，而中文欄名是 ZPlanner
#: 的顯示字串、隨時可能為了報表好看而改。但**對不上一律拋**（見
#: `_parse_export_csv`）——這裡換名字，不代表可以容忍欄位消失。
_EXPORT_COLUMNS = {
    "日期": "date",
    "人員": "user_name",
    "專案代碼": "project_code",
    "Issue 編號": "issue_key",
    "Issue 標題": "issue_title",
    "工時(h)": "hours",
    "備註": "note",
}


class ZPlannerClient:
    """ZPlanner API 的薄包裝。

    用法：

        client = ZPlannerClient()
        ok, reason = client.available()
        if not ok:
            ...                      # reason 是可以直接顯示給人看的中文
        issues = client.list_worklogable_issues("PEI")

    **關於 token 的身分歸屬**：token 從環境變數 `ZPLANNER_APIKEY` 讀，代表的是
    「跑這個服務的那個人」。這套專案的散佈方式是**每位同事各自 clone、各自部署、
    各自設自己的 token**，所以這裡只有一把 token 並不與 Draft Reply「以 Viewer
    本人身分送出」的隱喻衝突——一個部署就是一個人。

    即便如此，`token` 與 `base_url` 仍刻意做成**建構子參數**而不是只讀 config：
    測試要能注入假值而不依賴執行環境（這很重要，見下面 available 的說明），而且
    萬一將來改成共用部署，上層傳入各自的 token 即可，不必重構這一層。
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[Tuple[float, float]] = None,
        session: Optional[requests.Session] = None,
    ):
        raw_token = token if token is not None else cfg.ZPLANNER_APIKEY
        # strip 不是防呆而是**必要**：`export ZPLANNER_APIKEY=$(cat token.txt)`
        # 這種常見寫法會帶進尾端換行，而帶換行的標頭值會讓 requests 拋
        # InvalidHeader，**並把整個標頭值（含 token）寫進例外訊息**。
        self._token = (raw_token or "").strip()
        # 與 token 一樣用 `is not None` 判斷，**不可以用 `or`**：`or` 會把明確傳進來
        # 的空字串當成「沒有傳」而掉回環境變數，於是「給我一個沒有網址的 client」
        # 這個意圖被安靜地推翻。後果不只在測試裡——它讓 available() 在該回
        # 「未設定」的時候回「可以用」，而那正是這個方法存在的理由。
        # 2026-09-14 實測：開發機上有設 ZPLANNER_BASE_URL 時，
        # test_沒設_base_url_時不送出請求 必然紅燈，CI 上卻是綠的。
        raw_base = base_url if base_url is not None else cfg.ZPLANNER_BASE_URL
        self._base_url = (raw_base or "").strip().rstrip("/")
        self._timeout = timeout or cfg.ZPLANNER_TIMEOUT
        # 允許注入 session 純粹是為了測試時掛 mock；正常情況自己建一個以重用連線
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    # 可用性
    # ------------------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        """能不能用，以及不能用的話該怎麼辦。

        照 `core/providers/base.AIProvider.available` 的慣例回 (bool, 原因)。
        **只檢查設定，不打網路**——呼叫端常在渲染頁面時問這個，不該為了回答
        它去等一次 HTTP。token 是不是真的有效，要等第一次呼叫才知道（會拋
        ZPlannerAuthError）。

        網址與 token 都沒有預設值（見 config 的說明），所以兩者都要檢查。
        先查網址：沒有網址時連「要打去哪裡」都不知道，而那個錯誤如果留到送出
        請求才爆，訊息會是一句看不懂的 InvalidURL。
        """
        if not self._base_url:
            return (
                False,
                "未設定 ZPLANNER_BASE_URL。這個值刻意不進版控（見 core/config.py），"
                "請 export ZPLANNER_BASE_URL='https://你們的-zplanner-網址' 後重啟服務。",
            )
        if not self._token:
            return (
                False,
                "未設定 ZPLANNER_APIKEY。到 ZPlanner 的 /api/tokens/ 建立一把 "
                "token（明文只會顯示一次），export ZPLANNER_APIKEY='zp_…' 後重啟服務。",
            )
        if not _TOKEN_PATTERN.fullmatch(self._token):
            # **驗完整格式而不是只驗 zp_ 前綴。** 只驗前綴會放過「複製時少抓了
            # 幾個字」的 token，那種 token 一路送到 ZPlanner 才被擋，回來的是
            # 一句「請先登入」——看起來像權限問題，實際上是貼錯，很難查。
            # schema 宣告的 bearerFormat 就是 zp_ 加 64 個十六進位字元。
            return (
                False,
                "ZPLANNER_APIKEY 格式不對：應該是 zp_ 開頭加 64 個十六進位字元"
                f"（目前長度 {len(self._token)}）。請確認複製時沒有截斷。",
            )
        return True, "使用 ZPLANNER_APIKEY"

    # ------------------------------------------------------------------
    # 底層請求
    # ------------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        """把 token 從準備往上拋的文字裡抹掉。

        **這不是可有可無的保險。** requests 的例外訊息會包含整個標頭值——例如
        token 尾端帶換行時拋的 InvalidHeader，訊息裡就有完整的 `Bearer zp_…`。
        而 `ChatPulseError.detail` 會被上層寫進 log（見 dashboard 的錯誤處理），
        所以少了這一步，一次設定失誤就足以把 token 留在日誌檔裡。
        """
        if self._token and self._token in text:
            return text.replace(self._token, "zp_***已遮蔽***")
        return text

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ):
        """送一次請求，回傳 requests 的 response；只處理設定與傳輸層的失敗。

        這個方法是整個模組唯一碰 HTTP 的地方。**它刻意不看回應內容**——因為
        不是每支端點都回 `{code, message, data}` 信封（`export` 成功時回的是
        CSV），信封的解讀屬於 `_request`，CSV 的解讀屬於 `_request_csv`。

        **傳輸層失敗的錯誤型別依「這是不是寫入」而不同**，見下面的註解。
        判準是 HTTP method，不是呼叫端自己說——公開方法不可能忘記標記。
        """
        ok, reason = self.available()
        if not ok:
            # 沒設或格式明顯不對，兩種都是部署設定的問題，不是呼叫端的錯，
            # 也不值得為它送一次必定失敗的請求。
            raise ConfigurationError(reason)

        # 「會改變 ZPlanner 狀態的請求」。用 method 判定而不是多一個參數：
        # 參數會被忘記帶，method 不會。
        is_write = method.upper() not in ("GET", "HEAD", "OPTIONS")

        url = f"{self._base_url}{path}"
        try:
            resp = self._session.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                params=params,
                json=json_body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            # 🔴 **寫入的傳輸層失敗一律是「不知道有沒有寫進去」。**
            #
            # 逾時顯然如此；但連 ConnectionError 也算——它涵蓋「送出去了、
            # 對方處理完了、回應讀到一半斷線」。我們分不出那和「根本沒送到」，
            # 而分不出的時候唯一安全的假設是「可能已經寫進去了」。自動重送
            # 會在 ZPlanner 那邊多一筆，月結後刪不掉（要走 correction-request）。
            #
            # 唯讀查詢沒有這個問題，重試無害，維持 ZPLANNER_UNAVAILABLE。
            detail = self._scrub(f"{method} {path}: {exc}")
            if is_write:
                raise ZPlannerWriteIndeterminate(detail=detail) from exc
            if isinstance(exc, requests.Timeout):
                raise ZPlannerUnavailable("ZPlanner 連線逾時", detail=detail) from exc
            raise ZPlannerUnavailable(detail=detail) from exc
        return resp

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """送一次請求，回傳成功時的 `data` 欄位；任何失敗都拋例外。

        那條「狀態碼恆 200」的規則只在這裡處理一次，公開方法不必各自記得。
        """
        resp = self._send(method, path, params=params, json_body=json_body)

        # **先看 body，不先看 HTTP 狀態碼。**
        #
        # 直覺會想先把非 200 的 HTTP 擋掉，但那樣等於把這支 API 最重要的訊號
        # 讓給一個我們控制不了的東西：中介層（反向代理、WAF、閘道）隨時可能
        # 自己回一個 401 或 404，那與 ZPlanner 應用層想說的話無關。只要 body
        # 是合法的 {code, message, data} 信封，它就是唯一權威——不管外面包著
        # 什麼狀態碼。狀態碼只在「拿不到信封」時才派得上用場。
        try:
            payload = resp.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict) and isinstance(payload.get("code"), int):
            code = payload["code"]
            if code != 200:
                log.info("ZPlanner %s %s 回 code=%s", method, path, code)
                raise classify_zplanner_error(code, payload.get("message", ""))
            return payload.get("data")

        # 沒有合法信封。到這裡才輪到 HTTP 狀態碼，而且只用來分「值不值得重試」：
        # 5xx 與 429 是對方暫時有事，4xx 是我們這邊的問題（路徑打錯、被閘道擋），
        # 後者重試一萬次也一樣。
        snippet = self._scrub(resp.text[:400])
        if resp.status_code >= 500 or resp.status_code == 429:
            raise ZPlannerUnavailable(
                f"ZPlanner 回傳 HTTP {resp.status_code}，且不是預期的回應信封",
                detail=snippet,
            )
        if resp.status_code != 200:
            raise ZPlannerApiError(
                f"ZPlanner 回傳 HTTP {resp.status_code}，且不是預期的回應信封"
                "（多半是路徑打錯或被閘道擋下，重試不會好）",
                detail=snippet,
            )

        # HTTP 200 卻拿不到信封——這是「ZPlanner 改了回應格式」的典型徵兆
        if payload is None:
            raise ZPlannerApiError("ZPlanner 回應不是合法的 JSON", detail=snippet)
        if not isinstance(payload, dict):
            raise ZPlannerApiError(
                "ZPlanner 回應不是預期的物件信封",
                detail=self._scrub(repr(payload)[:400]),
            )
        # 沒有 code 就**沒有任何管道**可以判斷這次呼叫成功了沒有。
        # 猜「大概是成功吧」正是這個整合最貴的失敗模式，所以一律拋。
        raise ZPlannerApiError(
            "ZPlanner 回應缺少 code 欄位，無法判斷成敗",
            detail=self._scrub(repr(payload)[:400]),
        )

    def _get_paged(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """列舉一個會分頁的端點，把所有頁抓完併成一個 list。

        分頁的三個坑都在這裡處理：參數名是 `per_page`、容器 key 兩種都要吃、
        以及用 total_pages 而不是總筆數當終止條件。
        """
        collected: List[Dict[str, Any]] = []
        page = 1
        while page <= cfg.ZPLANNER_MAX_PAGES:
            query = dict(params or {})
            query["page"] = page
            # **不是 page_size。** 送錯名字不會報錯，只會安靜地每頁回 20 筆，
            # 於是「資料看起來變少了」要查很久才查得到這裡。
            query["per_page"] = min(cfg.ZPLANNER_PAGE_SIZE, cfg.ZPLANNER_PAGE_SIZE_MAX)

            data = self._request("GET", path, params=query)
            batch, total_pages = _unwrap_page(data, path)
            collected.extend(batch)

            if total_pages is None or page >= total_pages:
                return collected
            page += 1

        # 走到這裡代表 total_pages 一直說還有下一頁。
        #
        # **拋錯而不是截斷後回傳。** 截斷過的 list 與完整的 list 在呼叫端眼中
        # 長得一模一樣，而 log 裡的 warning 傳不到呼叫端手上——那就是一次
        # 靜默的資料遺失，與這個模組拒絕「猜成功」的理由是同一條。
        # 真的遇到超過一萬筆的正當情況，要調的是 MAX_PAGES，不是默默少給資料。
        raise ZPlannerApiError(
            f"ZPlanner {path} 的分頁超過 {cfg.ZPLANNER_MAX_PAGES} 頁的安全閥，"
            f"已讀到 {len(collected)} 筆仍未結束（可能是 total_pages 欄位異常）",
            detail=f"max_pages={cfg.ZPLANNER_MAX_PAGES}",
        )

    # ------------------------------------------------------------------
    # Issue
    # ------------------------------------------------------------------

    def list_worklogable_issues(self, q: str) -> List[Dict[str, Any]]:
        """可以填工時的 issue。**這是決定「能不能填」的唯一權威來源。**

        `q` 必填。空字串不會回傳全部，而是回 0 筆——而且 OpenAPI schema 根本
        沒宣告這個參數，照文件寫的人必然踩到，然後得到「我沒有任何可填工時的
        issue」這個完全錯誤的結論。所以這裡直接擋下來，不讓它安靜地回空陣列。

        另外注意：這個集合與 `list_my_issues()` **不一樣**，而且可以比它更多
        （有填報權限不等於被指派）。要填工時一律以這支為準。
        """
        keyword = (q or "").strip()
        if not keyword:
            # 用 INVALID_PARAMETER 而不是 ZPLANNER_INVALID_PARAMETER：照
            # errors.py 自己劃的判準，後者是「ZPlanner 那側的欄位規則被違反」，
            # 而這裡請求根本還沒送出去，是呼叫端沒給必填參數。
            raise InvalidParameter(
                "查詢可填工時的 issue 時必須給關鍵字：ZPlanner 對空字串會回 0 筆，"
                "而不是回傳全部。"
            )
        data = self._request("GET", "/api/issues/worklogable/", params={"q": keyword})
        return _as_list(data, "/api/issues/worklogable/")

    def list_my_issues(self) -> List[Dict[str, Any]]:
        """指派給我的 issue。

        ⚠️ **不要拿這裡的 id 去填工時。** 這個集合與 worklogable 互有增減：
        被指派不代表有填報權限。拿這裡的 id 硬填，會在送出時才被 403 擋掉。
        """
        data = self._request("GET", "/api/issues/mine/")
        return _as_list(data, "/api/issues/mine/")

    # ------------------------------------------------------------------
    # 專案
    # ------------------------------------------------------------------

    def list_projects(self, q: Optional[str] = None) -> List[Dict[str, Any]]:
        """專案列表，用來把專案代號解析成 project id。

        `q` 在這支是選填的（與 worklogable 不同，那支必填）。
        """
        params: Dict[str, Any] = {}
        keyword = (q or "").strip()
        if keyword:
            params["q"] = keyword
        data = self._request("GET", "/api/projects/", params=params or None)
        return _as_list(data, "/api/projects/")

    def list_project_issues(
        self,
        project_id: int,
        *,
        q: Optional[str] = None,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
        include_frozen: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """某專案底下的 issue（會自動翻完所有頁）。

        篩選參數照 ZPlanner 實際支援的那幾個開放，沒開放的不要自己加——
        這支 API 對不認得的 query 參數是**靜默忽略**，加了會得到「篩選沒有
        生效」而不是錯誤。
        """
        params: Dict[str, Any] = {}
        if q and q.strip():
            params["q"] = q.strip()
        if status:
            params["status"] = status
        if assignee:
            params["assignee"] = assignee
        if include_frozen is not None:
            params["include_frozen"] = "true" if include_frozen else "false"
        pid = _require_int(project_id, "project_id")
        return self._get_paged(f"/api/projects/{pid}/issues/", params=params)

    def list_project_role_permissions(self, project_id: int) -> List[Dict[str, Any]]:
        """該專案每個角色的權限旗標（can_worklog、can_create_issue…）。

        **寫入前先查這個，不要試打。** ZPlanner 的權限是每個專案各自一張角色
        矩陣、由該專案 PM 自訂，同一把 token 在 A 專案能做的事在 B 專案不一定
        能做——所以不存在「查一次就能套用到全部專案」的結論。

        回傳形狀（2026-09-13 實測）：list，每筆是 `{role, can_worklog,
        can_create_issue, can_update_issue, can_delete_issue, can_assign,
        can_transition, can_add_member, can_readonly, can_view_finance}`。
        這裡刻意回原樣不做正規化——把它跟 members 的 role 對起來算出「我能不能
        填工時」是業務判斷，屬於上層的事。
        """
        pid = _require_int(project_id, "project_id")
        data = self._request("GET", f"/api/projects/{pid}/role-permissions/")
        return _as_list(data, f"/api/projects/{pid}/role-permissions/")

    def list_project_members(self, project_id: int) -> List[Dict[str, Any]]:
        """專案成員與各自的角色。配合 role-permissions 才知道自己能做什麼。

        回傳形狀（2026-09-13 實測）：list，每筆含 `role`、`user_email`、
        `user_name`、`user_id`、`joined_at`、`left_at` 等。`user_email` 是把
        ZPlanner 身分接回 git commit 作者信箱的那把鑰匙。
        """
        pid = _require_int(project_id, "project_id")
        data = self._request("GET", f"/api/projects/{pid}/members/")
        return _as_list(data, f"/api/projects/{pid}/members/")

    # ------------------------------------------------------------------
    # 工時
    # ------------------------------------------------------------------

    def list_weekly_worklogs(self, week_start: Optional[str] = None) -> Dict[str, Any]:
        """某一週**已經填進 ZPlanner** 的工時（不是本地草稿）。

        `week_start` 給 `YYYY-MM-DD`（省略就不送這個參數）。

        🔴 **但 `week_start` 完全無效——這支端點永遠只回「本週」。**

        參數照送（將來 ZPlanner 真的實作了就會自動開始生效，而且下游早就有
        防線，見下面），但呼叫端**絕對不可以假設自己拿得到指定的那一週**。

        2026-09-14（週一）實測，五種輸入回同一週：

            傳 2026-09-07 -> week_start=2026-09-14  week=38
            傳 2026-08-03 -> week_start=2026-09-14  week=38
            傳 2026-07-06 -> week_start=2026-09-14  week=38
            傳 2026-09-14 -> week_start=2026-09-14  week=38
            不傳          -> week_start=2026-09-14  week=38

        OpenAPI schema 對這支**沒有宣告任何 query 參數**，與實測一致。

        ⚠️ 這裡原本寫的是「它會把日期正規化到那一週的週一」，證據是 2026-09-13
        傳 `2026-09-08`（週二）回 `2026-09-07`（週一）。那是個**假陽性**：09-13
        當下的本週正好就是 09-07～09-13，所以「正規化到週一」與「參數被忽略、
        一律回本週」兩種解釋會產生一模一樣的輸出，單一樣本分不出來。要否證它
        只需要傳一個**不在本週內**的日期——這正是 rules/20 §7「這個觀察證明得了
        你要的結論嗎」的形態，別再用落在本週內的日期去驗這件事。

        **要讀非本週的工時，改用 `export_worklogs(date_from, date_to)`**，那是
        目前唯一能指定任意區間的管道。

        回傳的那一週仍然要讀回應裡的 `week_start`／`week_end`，不要拿自己送出去
        的值當答案——這一點在參數無效的前提下只會更重要。

        ⚠️ **`hours` 這個欄位名在兩個層級是不同型別**（同日實測）：
        `days[].hours` 是 float（`2.0`），而 `entries[].hours` 是**字串**
        （`"1.50"`）。混著用會在其中一邊安靜地算錯——而 ZPlanner 的寫入端點
        吃的是字串，所以字串那一邊才是權威。

        回應形狀（2026-09-13 實測）：

            {year, week, week_start, week_end,
             total_hours: float, target_hours: float, completion_pct: float,
             days: [{date, weekday: "Mon", hours: float, entries: [...]}]}

        `days` **固定七筆**（沒填工時的那天 `hours` 是 0、`entries` 是空陣列），
        `weekday` 是英文縮寫，要中文得自己轉。每筆 entry 有 `id`、`issue_id`、
        `issue_key`、`user_id`、`user_name`、`date`、`start_time`（可為 null）、
        `hours`、`note`、`is_correction`、`is_locked`、`created_at`。

        **`entries[].id` 就是修改與刪除要用的 worklog id**，也是本地草稿
        `worklog_draft_entries.zplanner_worklog_id` 要對上的那個值。
        """
        params: Dict[str, Any] = {}
        if week_start:
            params["week_start"] = week_start
        data = self._request("GET", "/api/worklogs/weekly/", params=params or None)
        if not isinstance(data, dict):
            # 這支回的是一個週報表物件，不是清單。形狀不對就拋，不要猜——
            # 猜錯的代價是週檢視顯示一週零工時，而使用者會以為自己真的沒填
            raise ZPlannerApiError(
                "ZPlanner /api/worklogs/weekly/ 回傳了預期外的資料形狀",
                detail=repr(data)[:400],
            )
        return data

    def export_worklogs(self, date_from: str, date_to: str) -> List[Dict[str, str]]:
        """**任意日期區間**內我自己的工時。這是讀非本週工時的唯一管道。

        `list_weekly_worklogs` 的 `week_start` 是無效的（見該方法），所以「上週
        我到底填了什麼」只能靠這支回答。用途有兩個，兩個都很關鍵：

        1. **送出前去重**：同一天同一個 issue 已經有工時了就不要再送一次。
           重複的工時在月結後刪不掉，只能走 correction-request。
        2. **對帳**：`ZPlannerWriteIndeterminate` 之後查一下，就知道那筆到底
           有沒有寫進去。

        ⚠️ **兩個參數都必填，而且名字是 `date_from`／`date_to`。** 寫成
        `start_date`／`end_date` 不會得到「參數名不對」，而是
        `code 400「日期格式錯誤，需為 YYYY-MM-DD」`——訊息會把人推去檢查日期
        格式，但日期格式明明是對的（2026-09-14 實測）。所以這裡在送出之前先擋。

        ⚠️ **成功時回的是 CSV，不是 JSON 信封**（`text/csv; charset=utf-8-sig`，
        有 BOM）。失敗時才回信封。所以這支不能走 `_request`。

        回傳把 CSV 的中文欄名正規化成穩定的鍵（見 `_EXPORT_COLUMNS`）：
        `date`、`user_name`、`project_code`、`issue_key`、`issue_title`、
        `hours`、`note`。值一律是**字串**，沒有轉數字——`hours` 轉 float 會在
        比對「這筆是不是已經填過」時因為 `2` 與 `2.0` 而錯開，要算數的人自己轉。

        範圍是**本人**的工時，不是全公司（2026-09-14 實測：同一區間該 issue 有
        其他同事的工時，但這支只回自己的 9 筆）。
        """
        for name, value in (("date_from", date_from), ("date_to", date_to)):
            if not (value or "").strip():
                raise InvalidParameter(
                    f"匯出工時必須同時給 date_from 與 date_to，缺了 {name}。"
                    "（少給或寫錯參數名時 ZPlanner 會回「日期格式錯誤」，"
                    "那個訊息會誤導人去檢查日期格式。）"
                )
        text = self._request_csv(
            "/api/worklogs/export/",
            params={"date_from": date_from.strip(), "date_to": date_to.strip()},
        )
        return _parse_export_csv(text)

    def list_issue_worklogs(self, issue_id: int) -> Tuple[List[Dict[str, Any]], Any]:
        """某個 issue 的**所有人**的工時，以及該 issue 的累計時數。

        回 `(worklogs, actual_hours)`。與 `export_worklogs` 的差別很重要：
        那支只回自己的、可以指定日期區間；這支回全部人的、不能篩日期。
        要判斷「我自己填過了沒有」用 export，不要用這支去濾——2026-09-14 實測
        某個共用 issue（03CO 聯繫溝通討論）有 7182 筆，濾自己的成本很高。

        ⚠️ 回應形狀是 `{actual_hours, worklogs}`，**沒有分頁欄位**。`worklogs`
        這個容器 key 是 2026-09-14 才補進 `_PAGE_ITEM_KEYS` 的，補之前這支會
        直接拋「找不到資料欄位」。
        """
        iid = _require_int(issue_id, "issue_id")
        data = self._request("GET", f"/api/issues/{iid}/worklogs/")
        rows = _as_list(data, f"/api/issues/{iid}/worklogs/")
        actual = data.get("actual_hours") if isinstance(data, dict) else None
        return rows, actual

    # ------------------------------------------------------------------
    # 工時：寫入
    #
    # 🔴 這一區的每個方法失敗時都可能拋 ZPlannerWriteIndeterminate，意思是
    # 「不知道有沒有寫進去」。**收到它一律不准自動重試**，要先用
    # export_worklogs 對帳。理由見 core/errors.py 的該類別說明。
    # ------------------------------------------------------------------

    def create_worklog(
        self,
        issue_id: int,
        date: str,
        hours: str,
        *,
        note: Optional[str] = None,
        start_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """填一筆工時。回傳 ZPlanner 建好的那筆（含 `id`）。

        `issue_id` 必須來自 `list_worklogable_issues()`——那是「能不能填」的
        唯一權威。拿 `list_my_issues()` 的 id 硬填會在這裡被 403 擋掉。

        成功時 `data` 含 `id`、`issue_key`、`user_name`、`is_locked`；
        **`id` 就是 `worklog_draft_entries.zplanner_worklog_id` 要存的值**，
        存了才有辦法在重送時認出「這筆已經送過了」。

        2026-09-14 用這支實際填了 13 筆（worklog_id 116847–116859）。
        """
        body: Dict[str, Any] = {
            "issue_id": _require_int(issue_id, "issue_id"),
            "date": _require_date(date, "date"),
            "hours": require_hours(hours),
        }
        # note 與 start_time 是選填。**None 就整個不送**，不要送 null——
        # 選填欄位送 null 與不送在很多框架裡不是同一件事，而我們沒有理由賭。
        if note is not None:
            body["note"] = note
        if start_time is not None:
            body["start_time"] = start_time
        data = self._request("POST", "/api/worklogs/", json_body=body)
        if not isinstance(data, dict):
            raise ZPlannerApiError(
                "ZPlanner 建立工時後回傳了預期外的資料形狀"
                "（拿不到 id 就無法把它記回草稿，等同一筆孤兒工時）",
                detail=repr(data)[:400],
            )
        return data

    def update_worklog(
        self,
        worklog_id: int,
        *,
        hours: Optional[str] = None,
        note: Optional[str] = None,
        date: Optional[str] = None,
        start_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """改一筆已經存在的工時（PATCH，只送有給的欄位）。

        ⚠️ 月結鎖定（`is_locked`）的工時改不動，唯一途徑是
        `POST /api/worklogs/{id}/correction-requests/`（要人審核，不是一次
        呼叫就結束）。這裡不替呼叫端判斷鎖沒鎖——那是業務判斷，屬於上層。

        **一個欄位都沒給就直接擋下來**：送一個空的 PATCH 會得到 200，看起來
        像成功了，實際上什麼都沒改。那種「成功」比失敗更難查。
        """
        body: Dict[str, Any] = {}
        if hours is not None:
            body["hours"] = require_hours(hours)
        if date is not None:
            body["date"] = _require_date(date, "date")
        if note is not None:
            body["note"] = note
        if start_time is not None:
            body["start_time"] = start_time
        if not body:
            raise InvalidParameter(
                "更新工時至少要給一個欄位（hours／note／date／start_time）。"
                "空的 PATCH 會回成功但什麼都沒改。"
            )
        wid = _require_int(worklog_id, "worklog_id")
        data = self._request("PATCH", f"/api/worklogs/{wid}/", json_body=body)
        return data if isinstance(data, dict) else {}

    def delete_worklog(self, worklog_id: int) -> None:
        """刪一筆工時。

        已經刪過的再刪一次會拿到 `code 404` → `ZPlannerNotFound`。**呼叫端不該
        把它當成錯誤吞掉**：404 代表「現在確實沒有這筆了」，與想要的結果一致，
        但也可能代表 id 根本記錯了——這裡不替上層決定，照拋。
        """
        wid = _require_int(worklog_id, "worklog_id")
        self._request("DELETE", f"/api/worklogs/{wid}/")

    # ------------------------------------------------------------------
    # CSV 端點
    # ------------------------------------------------------------------

    def _request_csv(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> str:
        """送一次 GET，回傳 CSV 原文；失敗時照樣拋。

        與 `_request` 的差別只有一個：這支的**成功**回應不是 JSON 信封。
        但**失敗**時仍然是信封（`{"code":400,…}`），所以順序必須是
        「先試著解信封，解得出來就代表失敗了」——反過來先當 CSV 解的話，
        錯誤信封會被當成一份只有一行的 CSV，然後靜默回傳 0 筆。
        """
        resp = self._send("GET", path, params=params)

        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("code"), int):
            code = payload["code"]
            if code != 200:
                log.info("ZPlanner GET %s 回 code=%s", path, code)
                raise classify_zplanner_error(code, payload.get("message", ""))
            # 信封而且 code 200——這支端點不該這樣回。當成格式變更拋出來，
            # 不要猜 data 裡面是不是 CSV。
            raise ZPlannerApiError(
                f"ZPlanner {path} 回了 JSON 信封而不是 CSV，回應格式可能改了",
                detail=self._scrub(repr(payload)[:400]),
            )

        if resp.status_code >= 500 or resp.status_code == 429:
            raise ZPlannerUnavailable(
                f"ZPlanner 回傳 HTTP {resp.status_code}",
                detail=self._scrub(resp.text[:400]),
            )
        if resp.status_code != 200:
            raise ZPlannerApiError(
                f"ZPlanner 回傳 HTTP {resp.status_code}",
                detail=self._scrub(resp.text[:400]),
            )
        return resp.text


# ----------------------------------------------------------------------
# 回應形狀的處理
#
# 拆成模組級函式而不是方法，因為它們只依賴傳進來的 payload——測試時不必先
# 建一個 client、也不必掛任何 mock 就能單獨驗。
# ----------------------------------------------------------------------


def require_hours(value: Any) -> str:
    """把 `hours` 驗成 ZPlanner 收得下的字串。

    🔴 **只收字串，不收 float。** 這不是龜毛：`allocate()` 算出來的是
    0.5 的倍數，但 float 一旦進到格式化就可能變成 `2.0000000001` 或科學記號，
    而 ZPlanner 對格式不符只回一句「hours/date/start_time 格式錯誤」，
    看不出是哪個欄位、哪裡不對。DB 的 `worklog_draft_entries.hours` 本來就是
    TEXT，整條路上維持字串才不會有地方偷偷做浮點運算。

    實際使用的 pattern 是 `_HOURS_PATTERN`：`-?(?=.*\\d)\\d{0,3}(?:\\.\\d{0,2})?`
    （以 `fullmatch` 比對；最多三位整數、兩位小數）。**負數是合法的**——ZPlanner
    用它做沖銷，所以這裡不擋。`(?=.*\\d)` 這段 lookahead 要求至少一位數字，所以
    `""`、`"."`、`"-"`、`"-."`、只有空白的字串都會被拒絕。

    ⚠️ 舊版 docstring 引用的是沒有 lookahead 的簡化版
    `^-?\\d{0,3}(?:\\.\\d{0,2})?$`，曾讓人誤判空字串會被收下（2026-09-25）。
    要判斷這裡收不收某個值，看 `_HOURS_PATTERN` 常數本身，不要看註解。

    公開（不帶底線）是因為 `PUT /worklogs/{date}` 也要用它驗 Viewer 改過的
    時數（D-13）：格式錯的值若在 PUT 收下，要到送出時才變成單筆 `failed`，
    那時使用者早就離開編輯畫面了。被否決的做法：server 跨模組 import 私有名稱
    （改名時沒人知道外面有人在用）、把 pattern 複製一份到 server（兩份早晚會
    分岔，而分岔的症狀正是「PUT 收下、送出被拒」）。
    """
    if not isinstance(value, str):
        raise InvalidParameter(
            f"hours 必須是字串（例如 \"1.50\"），收到 {type(value).__name__} "
            f"{value!r}。ZPlanner 的寫入端點吃字串，用 float 會在格式化時"
            "產生它不收的表示法。"
        )
    text = value.strip()
    if not _HOURS_PATTERN.fullmatch(text):
        raise InvalidParameter(
            f"hours 格式不符 ZPlanner 的規則（最多三位整數、兩位小數）：{value!r}"
        )
    return text


def _require_date(value: Any, name: str) -> str:
    """把日期驗成 `YYYY-MM-DD`。

    只驗形狀不驗真實性（2026-02-31 這裡過得了，ZPlanner 會擋）。目的是把
    「傳了 datetime 物件」「傳了 2026/09/07」這種一眼可見的錯擋在送出之前——
    送出去只會換回一句分不出欄位的「格式錯誤」。
    """
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value.strip()):
        raise InvalidParameter(f"{name} 必須是 YYYY-MM-DD 格式的字串，收到 {value!r}")
    return value.strip()


def _parse_export_csv(text: str) -> List[Dict[str, str]]:
    """把匯出的 CSV 轉成 list of dict，中文欄名換成穩定的鍵。

    **欄位對不上就拋，不要回半套。** 少一欄而照樣回傳的話，呼叫端拿到的
    `issue_key` 會是 None，然後「這筆填過了沒有」的比對會全部落空——結果是
    重複填一整週的工時，而過程中沒有任何錯誤訊息。
    """
    # utf-8-sig 的 BOM：requests 依 charset 解碼後有時仍留在字串開頭。
    # csv 模組不會幫忙處理，留著的話第一個欄名會變成 "﻿日期"。
    body = (text or "").lstrip("﻿")
    if not body.strip():
        raise ZPlannerApiError("ZPlanner 匯出工時回了空白內容，連標題列都沒有")

    reader = csv.DictReader(io.StringIO(body))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    missing = [zh for zh in _EXPORT_COLUMNS if zh not in headers]
    if missing:
        raise ZPlannerApiError(
            f"ZPlanner 匯出工時的 CSV 缺少欄位 {missing}，回應格式可能改了",
            detail=f"實際欄位：{headers}",
        )

    rows: List[Dict[str, str]] = []
    for raw in reader:
        rows.append(
            {key: (raw.get(zh) or "").strip() for zh, key in _EXPORT_COLUMNS.items()}
        )
    return rows


def _require_int(value: Any, name: str) -> int:
    """把要插進 URL 路徑的參數強制成整數。

    不做這件事的話，呼叫端給的任何字串都能決定我們實際打去哪個端點
    （`197/../../something` 之類）。ZPlanner 的 id 一律是數字，轉不成就是
    呼叫端錯了，擋在送出之前比送出去再看 404 清楚。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InvalidParameter(f"{name} 必須是整數，收到 {value!r}") from None


def _as_list(data: Any, path: str) -> List[Dict[str, Any]]:
    """把「應該是一個清單」的 data 正規化成 list。

    有些端點直接回 list，有些包在分頁容器裡。兩種都吃，但**認不出來就拋**——
    回一個空 list 會讓呼叫端以為「查詢成功但沒有資料」，那是這支 API 上最容易
    誤導人的結論（見 list_worklogable_issues 的 q 參數）。
    """
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items, total_pages = _unwrap_page(data, path)
        if total_pages is not None and total_pages > 1:
            # 這支端點開始分頁了，但呼叫它的方法沒有翻頁邏輯。
            # **寧可拋，也不能安靜地只回第一頁**——少掉的那幾百筆在呼叫端
            # 看起來與「本來就只有這些」完全一樣。對 list_worklogable_issues
            # 尤其致命：它自稱是「能不能填工時」的唯一權威來源，漏資料會直接
            # 變成「我沒有填報權限」這個錯誤結論，正是本模組要防的那種誤導。
            raise ZPlannerApiError(
                f"ZPlanner {path} 回了 {total_pages} 頁的分頁結果，"
                "但這個方法只讀第一頁——要改用會翻頁的 _get_paged",
                detail=f"total_pages={total_pages}",
            )
        return items
    raise ZPlannerApiError(
        f"ZPlanner {path} 回傳了預期外的資料形狀", detail=repr(data)[:400]
    )


def _unwrap_page(data: Any, path: str) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """從分頁容器裡取出 (這一頁的資料, 總頁數)。

    總頁數可能是 None，代表這個端點沒有分頁、一次就回完了。
    """
    if data is None:
        return [], None
    if isinstance(data, list):
        # 沒有分頁容器的端點（worklogable、mine）走這條
        return data, None
    if not isinstance(data, dict):
        raise ZPlannerApiError(
            f"ZPlanner {path} 回傳了預期外的資料形狀", detail=repr(data)[:400]
        )

    for key in _PAGE_ITEM_KEYS:
        if isinstance(data.get(key), list):
            total_pages = data.get(_TOTAL_PAGES_KEY)
            return data[key], total_pages if isinstance(total_pages, int) else None

    raise ZPlannerApiError(
        f"ZPlanner {path} 的分頁容器找不到資料欄位"
        f"（認得 {'、'.join(_PAGE_ITEM_KEYS)}，實際拿到 {sorted(data)[:10]}）",
        detail=repr(data)[:400],
    )
