"""`ZPlannerClient`：HTTP 狀態碼恆 200 的 API 要怎麼判斷成敗。

**這一套測試的存在理由只有一個**：ZPlanner 成功與失敗都回 HTTP 200，真正的
狀態在 response body 的 `code` 欄位。任何一次重構若不小心引入
`raise_for_status()`、`if resp.ok`、或「解析不出 code 就當成功」，這個整合會
**安靜地**把權限不足與 token 失效當成成功，帶著 `data: null` 往下跑。

那種壞法不會有任何跡象，所以守住它的責任在這裡，不在 code review。這一套守住：

  1. HTTP 200 ＋ body code 403 → 一定要拋，不能回傳 None
  2. 讀不出 code（缺欄位、非 JSON）→ 一定要拋，**不准猜成功**
  3. body 信封優先於 HTTP 狀態碼；拿不到信封時狀態碼才用來分「值不值得重試」
  4. worklogable 的 q 空字串 → 擋在送出之前（ZPlanner 會回 0 筆而不是全部）
  5. 分頁參數是 per_page 不是 page_size，容器 key 兩種都要吃
  6. **任何形式的靜默少給資料都要拋**：認不出的容器、只讀到第一頁、超過安全閥
  7. token 不進錯誤訊息；網址與 token 都不進版控（這個 GitHub repo 是公開的）

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import os
import re
import sys
import unittest
from unittest import mock

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core import config as cfg  # noqa: E402
from core.errors import (  # noqa: E402
    ConfigurationError,
    InvalidParameter,
    ZPlannerApiError,
    ZPlannerAuthError,
    ZPlannerForbidden,
    ZPlannerInvalidParameter,
    ZPlannerNotFound,
    ZPlannerUnavailable,
)
from core.zplanner_client import ZPlannerClient  # noqa: E402

TOKEN = "zp_" + "a" * 64
#: 假網址。**測試絕不使用真實的內部網址**——這個 repo 是公開的。
BASE_URL = "https://zplanner.example.test"


class FakeResponse:
    """只實作 client 真正會碰到的三個面：status_code、json()、text。"""

    def __init__(self, payload=None, status_code=200, text=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises
        self.text = text if text is not None else repr(payload)

    def json(self):
        if self._raises:
            raise ValueError("No JSON object could be decoded")
        return self._payload


class FakeSession:
    """記錄每一次呼叫，並依序吐出預先排好的回應。"""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, params=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params,
                "timeout": timeout,
            }
        )
        if not self._responses:
            raise AssertionError(f"沒有預備給第 {len(self.calls)} 次呼叫的回應：{url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def envelope(data, code=200, message=""):
    """ZPlanner 的三欄位回應信封。"""
    return {"code": code, "message": message, "data": data}


def client(*responses, token=TOKEN, base_url=BASE_URL):
    """建一個掛著假 session 的 client。

    **base_url 與 token 都明確傳入，不讀 config。** 兩者現在都沒有 fallback
    預設值（刻意不進版控），所以任何依賴 config 的測試，在沒設環境變數的機器
    上會整批紅掉——那是測試依賴執行環境的錯，不是程式的錯。
    """
    session = FakeSession(*responses)
    return ZPlannerClient(token=token, base_url=base_url, session=session), session


# ----------------------------------------------------------------------
# 1. 核心：HTTP 200 但 body 說失敗
# ----------------------------------------------------------------------


class BodyCodeDecidesOutcome(unittest.TestCase):
    """HTTP 一律 200，成敗只看 body 的 code。"""

    def test_code_403_雖然_http_200_仍要拋(self):
        c, _ = client(FakeResponse(envelope(None, code=403, message="權限不足"), status_code=200))
        with self.assertRaises(ZPlannerForbidden) as ctx:
            c.list_my_issues()
        # ZPlanner 的原文要留在 detail，對外訊息用我們自己帶行動指引的那句
        self.assertEqual(ctx.exception.detail, "權限不足")
        self.assertIn("沒有這項權限", ctx.exception.message)

    def test_code_401_轉成_ZPlannerAuthError_而不是_NotAuthenticated(self):
        # 分開的理由：Viewer 重新登入 Google 修不好這個，要換的是伺服器的 token
        c, _ = client(FakeResponse(envelope(None, code=401, message="請先登入")))
        with self.assertRaises(ZPlannerAuthError) as ctx:
            c.list_my_issues()
        self.assertEqual(ctx.exception.http_status, 500)

    def test_code_404(self):
        c, _ = client(FakeResponse(envelope(None, code=404, message="找不到工時")))
        with self.assertRaises(ZPlannerNotFound):
            c.list_my_issues()

    def test_code_400(self):
        c, _ = client(FakeResponse(envelope(None, code=400, message="hours 格式錯誤")))
        with self.assertRaises(ZPlannerInvalidParameter):
            c.list_my_issues()

    def test_未知的_code_也要拋而不是當成功(self):
        c, _ = client(FakeResponse(envelope({"x": 1}, code=418, message="???")))
        with self.assertRaises(ZPlannerApiError) as ctx:
            c.list_my_issues()
        self.assertIn("418", ctx.exception.message)

    def test_code_200_才回傳_data(self):
        rows = [{"id": 1, "issue_key": "PEI-1"}]
        c, _ = client(FakeResponse(envelope(rows)))
        self.assertEqual(c.list_my_issues(), rows)


# ----------------------------------------------------------------------
# 2. 讀不出 code 時不准猜
# ----------------------------------------------------------------------


class UnreadableResponseMustRaise(unittest.TestCase):
    """「不知道成功了沒有」必須等同於失敗。"""

    def test_缺少_code_欄位(self):
        c, _ = client(FakeResponse({"data": [], "message": "ok"}))
        with self.assertRaises(ZPlannerApiError) as ctx:
            c.list_my_issues()
        self.assertIn("缺少 code", ctx.exception.message)

    def test_code_不是整數(self):
        c, _ = client(FakeResponse({"code": "200", "data": [], "message": ""}))
        with self.assertRaises(ZPlannerApiError):
            c.list_my_issues()

    def test_回應不是_JSON(self):
        c, _ = client(FakeResponse(None, text="<html>502 Bad Gateway</html>", raises=True))
        with self.assertRaises(ZPlannerApiError) as ctx:
            c.list_my_issues()
        self.assertIn("JSON", ctx.exception.message)

    def test_回應是_JSON_但不是物件(self):
        c, _ = client(FakeResponse([1, 2, 3]))
        with self.assertRaises(ZPlannerApiError):
            c.list_my_issues()


# ----------------------------------------------------------------------
# 3. HTTP 層的失敗與 body code 是兩件事
# ----------------------------------------------------------------------


class TransportFailures(unittest.TestCase):
    def test_http_非200_視為沒到達應用層(self):
        c, _ = client(FakeResponse(None, status_code=504, text="gateway timeout"))
        with self.assertRaises(ZPlannerUnavailable) as ctx:
            c.list_my_issues()
        self.assertIn("504", ctx.exception.message)

    def test_逾時(self):
        c, _ = client(requests.Timeout("timed out"))
        with self.assertRaises(ZPlannerUnavailable) as ctx:
            c.list_my_issues()
        self.assertIn("逾時", ctx.exception.message)

    def test_連線失敗(self):
        c, _ = client(requests.ConnectionError("no route to host"))
        with self.assertRaises(ZPlannerUnavailable):
            c.list_my_issues()

    def test_body_信封優先於_http_狀態碼(self):
        # 中介層（反向代理、WAF、閘道）隨時可能自己回一個狀態碼，那與 ZPlanner
        # 應用層想說的話無關。這裡 HTTP 說 401、body 說 403——以 body 為準。
        c, _ = client(
            FakeResponse(envelope(None, code=403, message="權限不足"), status_code=401)
        )
        with self.assertRaises(ZPlannerForbidden):
            c.list_my_issues()

    def test_http_500_但_body_是合法信封時仍照_body(self):
        c, _ = client(FakeResponse(envelope([{"id": 1}]), status_code=500))
        self.assertEqual(c.list_my_issues(), [{"id": 1}])

    def test_4xx_且拿不到信封是永久錯誤不是可重試(self):
        # 路徑打錯或被閘道擋掉，重試一萬次也一樣，不該給可重試的語意
        c, _ = client(FakeResponse(None, status_code=404, text="<html>404</html>"))
        with self.assertRaises(ZPlannerApiError) as ctx:
            c.list_my_issues()
        self.assertIn("404", ctx.exception.message)

    def test_429_視為可重試(self):
        c, _ = client(FakeResponse(None, status_code=429, text="slow down"))
        with self.assertRaises(ZPlannerUnavailable):
            c.list_my_issues()


# ----------------------------------------------------------------------
# 4. 認證與可用性
# ----------------------------------------------------------------------


class AuthHeaderAndAvailability(unittest.TestCase):
    def test_只送_Authorization_Bearer(self):
        c, session = client(FakeResponse(envelope([])))
        c.list_my_issues()
        headers = session.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        # 另外 11 種帶法實測全回 401，不該出現在請求裡
        for 不該有的 in ("X-API-Key", "X-Api-Token", "Api-Key", "apikey"):
            self.assertNotIn(不該有的, headers)

    def test_沒設_token_時不送出請求(self):
        c, session = client(token="")
        ok, reason = c.available()
        self.assertFalse(ok)
        self.assertIn("ZPLANNER_APIKEY", reason)
        with self.assertRaises(ConfigurationError):
            c.list_my_issues()
        self.assertEqual(session.calls, [], "沒有 token 就不該浪費一次必定失敗的請求")

    def test_token_格式明顯不對時擋下來(self):
        c, session = client(token="sessionid=abcdef")
        ok, _ = c.available()
        self.assertFalse(ok)
        with self.assertRaises(ConfigurationError):
            c.list_my_issues()
        self.assertEqual(session.calls, [])

    def test_沒設_base_url_時不送出請求(self):
        # 網址和 token 一樣沒有預設值（刻意不進版控）
        c, session = client(base_url="")
        ok, reason = c.available()
        self.assertFalse(ok)
        self.assertIn("ZPLANNER_BASE_URL", reason)
        with self.assertRaises(ConfigurationError):
            c.list_my_issues()
        self.assertEqual(session.calls, [])

    def test_截斷的_token_被擋下(self):
        # 只驗 zp_ 前綴的話這個會過關，然後在 ZPlanner 那邊拿到「請先登入」——
        # 看起來像權限問題，實際上是複製 token 時少抓了幾個字，很難查
        c, session = client(token="zp_" + "a" * 40)
        ok, reason = c.available()
        self.assertFalse(ok)
        self.assertIn("截斷", reason)
        self.assertEqual(session.calls, [])

    def test_token_尾端換行會被清掉(self):
        # export ZPLANNER_APIKEY=$(cat token.txt) 是常見寫法，會帶進尾端換行。
        # 不清掉的話 requests 會拋 InvalidHeader，而那個訊息含整段 token
        c, session = client(FakeResponse(envelope([])), token=TOKEN + "\n")
        self.assertTrue(c.available()[0])
        c.list_my_issues()
        self.assertEqual(
            session.calls[0]["headers"]["Authorization"], f"Bearer {TOKEN}"
        )

    def test_例外的_detail_不含_token(self):
        # detail 會被上層寫進 log，一次設定失誤就足以把 token 留在日誌裡
        c, _ = client(requests.ConnectionError(f"bad header: Bearer {TOKEN}"))
        with self.assertRaises(ZPlannerUnavailable) as ctx:
            c.list_my_issues()
        self.assertNotIn(TOKEN, ctx.exception.detail or "")
        self.assertNotIn(TOKEN, ctx.exception.message)
        self.assertIn("已遮蔽", ctx.exception.detail)

    def test_回應內容含_token_時也會遮蔽(self):
        c, _ = client(
            FakeResponse(None, status_code=500, text=f"upstream echoed Bearer {TOKEN}")
        )
        with self.assertRaises(ZPlannerUnavailable) as ctx:
            c.list_my_issues()
        self.assertNotIn(TOKEN, ctx.exception.detail or "")

    def test_available_不打網路(self):
        c, session = client()
        self.assertEqual(c.available(), (True, "使用 ZPLANNER_APIKEY"))
        self.assertEqual(session.calls, [])


# ----------------------------------------------------------------------
# 5. worklogable 的 q 必填
# ----------------------------------------------------------------------


class WorklogableRequiresQuery(unittest.TestCase):
    """空的 q 會回 0 筆而不是全部，所以要擋在送出之前。"""

    def test_空字串被擋下且不送出請求(self):
        for 空值 in ("", "   ", None):
            with self.subTest(q=空值):
                c, session = client()
                with self.assertRaises(InvalidParameter) as ctx:
                    c.list_worklogable_issues(空值)
                # 請求根本還沒送出去，所以這是呼叫端沒給必填參數，
                # 不是 ZPlanner 那側的欄位規則被違反（那個是 ZPLANNER_ 開頭）
                self.assertEqual(ctx.exception.code, "INVALID_PARAMETER")
                self.assertEqual(session.calls, [])

    def test_有關鍵字就照送(self):
        rows = [{"id": 42069}]
        c, session = client(FakeResponse(envelope(rows)))
        self.assertEqual(c.list_worklogable_issues("  PEI  "), rows)
        self.assertEqual(session.calls[0]["params"], {"q": "PEI"})
        self.assertTrue(session.calls[0]["url"].endswith("/api/issues/worklogable/"))


# ----------------------------------------------------------------------
# 6. 分頁
# ----------------------------------------------------------------------


class Pagination(unittest.TestCase):
    def test_用_per_page_不是_page_size(self):
        c, session = client(FakeResponse(envelope({"items": [], "total_pages": 1})))
        c.list_project_issues(646)
        params = session.calls[0]["params"]
        self.assertEqual(params["per_page"], cfg.ZPLANNER_PAGE_SIZE)
        self.assertEqual(params["page"], 1)
        # page_size 是無效參數，ZPlanner 收到會靜默忽略照樣回 20 筆
        self.assertNotIn("page_size", params)

    def test_per_page_即使設定被改大也會夾到上限(self):
        # 這裡原本只斷言 PAGE_SIZE <= PAGE_SIZE_MAX，但兩個常數目前同值，
        # 那個斷言恆為真、什麼都沒守到。要守的是 min() 真的有生效——
        # ZPlanner 對超過 200 的 per_page 是**靜默夾到 200**，不報錯，
        # 所以送超過去不會有任何跡象告訴我們設定寫錯了。
        with mock.patch.object(cfg, "ZPLANNER_PAGE_SIZE", 500):
            c, session = client(FakeResponse(envelope({"items": [], "total_pages": 1})))
            c.list_project_issues(646)
            self.assertEqual(
                session.calls[0]["params"]["per_page"], cfg.ZPLANNER_PAGE_SIZE_MAX
            )

    def test_items_容器(self):
        c, _ = client(FakeResponse(envelope({"items": [{"id": 1}], "total": 1, "total_pages": 1})))
        self.assertEqual(c.list_project_issues(646), [{"id": 1}])

    def test_results_容器(self):
        # 同一個 client 也要吃得下另一種 key，兩個端點各用一種
        c, _ = client(FakeResponse(envelope({"results": [{"id": 2}], "count": 1, "total_pages": 1})))
        self.assertEqual(c.list_project_issues(646), [{"id": 2}])

    def test_翻完所有頁(self):
        c, session = client(
            FakeResponse(envelope({"items": [{"id": 1}], "total_pages": 3})),
            FakeResponse(envelope({"items": [{"id": 2}], "total_pages": 3})),
            FakeResponse(envelope({"items": [{"id": 3}], "total_pages": 3})),
        )
        self.assertEqual(c.list_project_issues(646), [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual([call["params"]["page"] for call in session.calls], [1, 2, 3])

    def test_沒有_total_pages_就只抓一頁(self):
        c, session = client(FakeResponse(envelope({"items": [{"id": 1}]})))
        self.assertEqual(c.list_project_issues(646), [{"id": 1}])
        self.assertEqual(len(session.calls), 1)

    def test_total_pages_異常時拋錯而不是安靜截斷(self):
        # 永遠宣稱還有下一頁：沒有上限就會無限迴圈。
        # 但上限到了要**拋**而不是回傳讀到的部分——截斷過的 list 與完整的 list
        # 在呼叫端眼中一模一樣，log 裡的 warning 傳不到呼叫端手上。
        responses = [
            FakeResponse(envelope({"items": [{"id": i}], "total_pages": 9999}))
            for i in range(cfg.ZPLANNER_MAX_PAGES + 5)
        ]
        c, session = client(*responses)
        with self.assertRaises(ZPlannerApiError) as ctx:
            c.list_project_issues(646)
        self.assertEqual(len(session.calls), cfg.ZPLANNER_MAX_PAGES)
        self.assertIn("安全閥", ctx.exception.message)

    def test_認不出的分頁容器要拋而不是回空清單(self):
        # 回空清單會被讀成「查詢成功但沒有資料」——這支 API 上最容易誤導人的結論
        c, _ = client(FakeResponse(envelope({"rows": [{"id": 1}], "total_pages": 1})))
        with self.assertRaises(ZPlannerApiError) as ctx:
            c.list_project_issues(646)
        self.assertIn("分頁容器", ctx.exception.message)

    def test_不翻頁的端點若回多頁容器要拋而不是只給第一頁(self):
        # worklogable／mine／projects／members 走 _as_list，沒有翻頁邏輯。
        # 若 ZPlanner 哪天讓這些端點開始分頁，只回第一頁會靜默少掉上百筆，
        # 而呼叫端看到的結果與「本來就只有這些」完全一樣——正是本模組要防的
        # 那種誤導，只是換了一個入口進來。
        多頁 = {"results": [{"id": 1}], "count": 137, "total_pages": 7}
        for 端點, 呼叫 in (
            ("mine", lambda c: c.list_my_issues()),
            ("worklogable", lambda c: c.list_worklogable_issues("PEI")),
            ("projects", lambda c: c.list_projects()),
            ("members", lambda c: c.list_project_members(646)),
        ):
            with self.subTest(端點=端點):
                c, _ = client(FakeResponse(envelope(dict(多頁))))
                with self.assertRaises(ZPlannerApiError) as ctx:
                    呼叫(c)
                self.assertIn("只讀第一頁", ctx.exception.message)

    def test_單頁容器仍然正常回傳(self):
        # 上一條不可矯枉過正：total_pages=1 就是完整資料，不該拋
        c, _ = client(FakeResponse(envelope({"results": [{"id": 1}], "total_pages": 1})))
        self.assertEqual(c.list_my_issues(), [{"id": 1}])

    def test_篩選參數只送有給的(self):
        c, session = client(FakeResponse(envelope({"items": [], "total_pages": 1})))
        c.list_project_issues(646, q="登入", include_frozen=False)
        params = session.calls[0]["params"]
        self.assertEqual(params["q"], "登入")
        self.assertEqual(params["include_frozen"], "false")
        # 沒給的不要送空值，ZPlanner 對認不得或空的參數是靜默忽略
        self.assertNotIn("status", params)
        self.assertNotIn("assignee", params)


# ----------------------------------------------------------------------
# 7. 其他端點的路徑與形狀
# ----------------------------------------------------------------------


class EndpointShapes(unittest.TestCase):
    def test_專案列表的_q_是選填(self):
        c, session = client(FakeResponse(envelope([{"id": 646}])))
        self.assertEqual(c.list_projects(), [{"id": 646}])
        self.assertIsNone(session.calls[0]["params"])

    def test_專案列表帶_q(self):
        c, session = client(FakeResponse(envelope([])))
        c.list_projects("PEI")
        self.assertEqual(session.calls[0]["params"], {"q": "PEI"})

    def test_role_permissions_是_list_不是以角色為鍵的_dict(self):
        # 形狀照 2026-09-13 對正式站的實測：list，每筆有 role 與 9 個 can_*。
        # 這個 fixture 一開始寫成 {"PG": {...}} 的 dict，與實際回應相反——
        # 錯的 fixture 會把錯的形狀固化成規格，上層照著寫就會在正式站炸掉。
        payload = [
            {"role": "PG", "can_worklog": True, "can_create_issue": False},
            {"role": "guest", "can_worklog": False, "can_create_issue": False},
        ]
        c, session = client(FakeResponse(envelope(payload)))
        rows = c.list_project_role_permissions(646)
        self.assertEqual(rows, payload)
        self.assertEqual({r["role"] for r in rows}, {"PG", "guest"})
        self.assertTrue(session.calls[0]["url"].endswith("/api/projects/646/role-permissions/"))

    def test_members_路徑(self):
        c, session = client(FakeResponse(envelope([{"user": "mark", "role": "PG"}])))
        c.list_project_members(646)
        self.assertTrue(session.calls[0]["url"].endswith("/api/projects/646/members/"))

    def test_data_為_null_時回空清單(self):
        c, _ = client(FakeResponse(envelope(None)))
        self.assertEqual(c.list_my_issues(), [])

    def test_base_url_結尾斜線不會變成雙斜線(self):
        session = FakeSession(FakeResponse(envelope([])))
        c = ZPlannerClient(token=TOKEN, base_url="https://zplanner.example.com/", session=session)
        c.list_my_issues()
        self.assertEqual(
            session.calls[0]["url"], "https://zplanner.example.com/api/issues/mine/"
        )

    def test_逾時設定有帶進請求(self):
        c, session = client(FakeResponse(envelope([])))
        c.list_my_issues()
        self.assertEqual(session.calls[0]["timeout"], cfg.ZPLANNER_TIMEOUT)

    def test_project_id_不是整數時擋在送出之前(self):
        # project_id 直接插進 URL 路徑，不強制型別等於讓上游的任何字串
        # 決定我們實際打去哪個端點
        for 壞值 in ("646/../../something", "abc", None):
            with self.subTest(project_id=壞值):
                c, session = client()
                with self.assertRaises(InvalidParameter):
                    c.list_project_members(壞值)
                self.assertEqual(session.calls, [])

    def test_數字字串的_project_id_可以接受(self):
        # 不要矯枉過正：從 JSON 或 query string 拿到的 id 常常是字串
        c, session = client(FakeResponse(envelope([])))
        c.list_project_members("646")
        self.assertTrue(session.calls[0]["url"].endswith("/api/projects/646/members/"))


# ----------------------------------------------------------------------
# 8. 設定衛生：ZPlanner 的位置與憑證都不進版控
# ----------------------------------------------------------------------


class ConfigHygiene(unittest.TestCase):
    """網址與 token 都不可以有 fallback 預設值。

    這不是潔癖：**這個專案的 GitHub repo 是公開的**，而且要分享給同事。任何
    寫進檔案的東西都會跟著出去。處置方式直接沿用缺陷 D-2（Gemini 金鑰曾被
    硬編碼成 fallback）的結論，見 SPECIFICATION.md 3.3。
    """

    def _source(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_設定沒有硬編碼的_fallback(self):
        source = self._source("core/config.py")
        for 變數 in ("ZPLANNER_BASE_URL", "ZPLANNER_APIKEY"):
            with self.subTest(變數=變數):
                m = re.search(
                    rf'{變數}\s*=\s*os\.environ\.get\(\s*"{變數}"\s*,\s*([^)]*)\)',
                    source,
                )
                self.assertIsNotNone(m, f"找不到 {變數} 的定義（格式改了就更新這條）")
                self.assertIn(
                    m.group(1).strip(),
                    ('""', "''"),
                    f"{變數} 不可以有 fallback 預設值——這個 repo 是公開的",
                )

    def test_這次新增的檔案不含公司內部網域(self):
        # 關鍵字**刻意拆開拼**：直接寫成完整字串的話，這個測試檔自己就含有它，
        # 於是唯一會紅的就是測試檔本身（第一次跑就是這樣）。拆開之後這條規則
        # 才能連自己一起檢查。
        needle = "intu" + "mit"
        for rel in (
            "core/config.py",
            "core/zplanner_client.py",
            "scripts/zplanner_smoke.py",
            "tests/unit/test_zplanner_client.py",
            "docs/adr/0008-draft-worklog-over-clipboard-export.md",
            "README.md",
        ):
            with self.subTest(檔案=rel):
                # 用 assertFalse 而不是 assertNotIn：後者失敗時會把整個檔案
                # 內容當成錯誤訊息印出來，幾百行的雜訊反而蓋掉重點
                self.assertFalse(
                    needle in self._source(rel).lower(),
                    f"{rel} 含公司內部網域字樣——這個 repo 是公開的，不可以進版控",
                )


if __name__ == "__main__":
    unittest.main()
