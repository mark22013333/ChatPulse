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
    classify_zplanner_error,
)

log = logging.getLogger("chatpulse.zplanner")

#: schema 的 securitySchemes 宣告 bearerFormat 為 zp_ 加 64 個十六進位字元。
_TOKEN_PATTERN = re.compile(r"zp_[0-9a-fA-F]{64}")

#: 分頁容器裡「那一頁的資料」可能叫這些名字。**順序有意義**：先找專案 issue
#: 用的 items，再找跨專案 issue 用的 results。兩個端點各用一種，不是筆誤。
_PAGE_ITEM_KEYS = ("items", "results")

#: 總頁數的欄位。`total`（專案 issue）與 `count`（跨專案 issue）是總**筆數**
#: 不是頁數，所以不能拿來當迴圈終止條件——這裡只認 total_pages。
_TOTAL_PAGES_KEY = "total_pages"


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
        self._base_url = (base_url or cfg.ZPLANNER_BASE_URL or "").strip().rstrip("/")
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

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """送一次請求，回傳成功時的 `data` 欄位；任何失敗都拋例外。

        這個方法是整個模組唯一碰 HTTP 的地方，那條「狀態碼恆 200」的規則也
        只在這裡處理一次，公開方法不必各自記得。
        """
        ok, reason = self.available()
        if not ok:
            # 沒設或格式明顯不對，兩種都是部署設定的問題，不是呼叫端的錯，
            # 也不值得為它送一次必定失敗的請求。
            raise ConfigurationError(reason)

        url = f"{self._base_url}{path}"
        try:
            resp = self._session.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                params=params,
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            # 逾時**不保證請求沒有送達**。這一層全是唯讀 GET 所以重試無害，
            # 但錯誤型別要留住這個區別，等寫入端點接上來時才不會被誤判成
            # 「沒送出去，重送一次就好」。
            raise ZPlannerUnavailable(
                "ZPlanner 連線逾時", detail=self._scrub(f"{method} {path}: {exc}")
            ) from exc
        except requests.RequestException as exc:
            raise ZPlannerUnavailable(
                detail=self._scrub(f"{method} {path}: {exc}")
            ) from exc

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


# ----------------------------------------------------------------------
# 回應形狀的處理
#
# 拆成模組級函式而不是方法，因為它們只依賴傳進來的 payload——測試時不必先
# 建一個 client、也不必掛任何 mock 就能單獨驗。
# ----------------------------------------------------------------------


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
