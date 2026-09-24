"""Viewer 自己選 model 這條路，API 層有沒有真的接上去。

**為什麼需要這一套。** 這個功能錯掉的表現是**靜默的**：摘要照樣產得出來、
草稿照樣寫得出來，只是用的不是 Viewer 選的那個模型——畫面上看不出來，
要到 token_usage 的模型欄或帳單才對得出不一樣。

會靜默壞掉的有三處，各自守一組測試：

1. **三層生效順序**（body → Viewer 偏好 → None）。少了第 1 層，Viewer 選了等於
   沒選；少了第 2 層，「設為預設」存了等於沒存。
2. **端點到 `resolve_model()` 的接線**。兩個串流端點各自解析一份的話，改了
   一處另一處會悄悄不一致，所以這裡驗的是「body 的 model 有走到那一份」。
3. **PATCH 的鍵驗證與清除語意**。別名（`claude`／`auto`）當鍵存進去就對應不
   回來，要在門口擋掉；而 null／空字串必須真的清得掉，否則 Viewer 無法回到
   「跟著供應商預設走」。另外 merge 的**基底**不可以來自會吞例外的讀取
   helper，否則一次讀取失敗就靜默變成整份覆寫。

`core/` 的部分（`providers.resolve(model=...)`、`providers.MODEL_CHOICES`、
`providers.is_multi_candidate()`、`repo.get_default_models`／`set_default_models`）
由另一位實作，這裡一律用真的屬性去 patch（不加 `create=True`）——core 還沒寫完時
這些測試會直接報 AttributeError，那正是我們要看到的訊號，而不是讓測試假裝通過。
`is_multi_candidate()` 刻意**不 patch**：它的存在理由就是「別在別層抄一份別名
名單」，patch 掉就等於把要驗的東西換成假的。

執行：.venv/bin/python -m unittest discover -s tests/unit
"""

import asyncio
import json
import os
import sys
import unittest
from contextlib import ExitStack, contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.errors import InvalidParameter  # noqa: E402
from dashboard.api import server  # noqa: E402

VIEWER = {"id": 1, "google_user_id": "users/me", "display_name": "我"}


def preferences(**overrides):
    """偏好的替身。不 patch 的話單元測試會連到真的資料庫，而且結果會隨開發者
    自己的偏好設定而變（同 test_draft_stream_wiring 的處置）。"""
    base = {
        "pinned_space_ids": [],
        "default_limit": 50,
        "default_style": "general",
        "default_provider": None,
        "default_code_project_id": None,
        "default_code_environment": None,
        "default_reply_tone": None,
        "default_persona_id": None,
        "default_reply_prompt_id": None,
        "default_sepia_enabled": None,
        "default_models": {},
        "updated_at": "2026-09-17T00:00:00+00:00",
    }
    base.update(overrides)
    return base


@contextmanager
def fake_core(prefs=None, implementation="claude_cli", resolve_name=None):
    """把 API 層與 core／DB 隔開。

    `resolve_name` 給 callable 就直接用它（要測「供應商不可用時會拋」的情況），
    否則一律回 `implementation`——第 2 層查偏好用的鍵就是這個展開後的實作名。
    """
    prefs = prefs if prefs is not None else preferences()
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(server.repo, "get_preferences", lambda vid: dict(prefs))
        )
        stack.enter_context(
            mock.patch.object(
                server.providers,
                "resolve_name",
                resolve_name or (lambda name=None: implementation),
            )
        )
        stack.enter_context(
            mock.patch.object(server.providers, "default_name", lambda: "claude")
        )
        yield


class ResolveModelTests(unittest.TestCase):
    """先展開、再查偏好的生效順序（契約第三節）。

    `resolve_model()` 回傳 `(供應商名, model 裸值)`。
    """

    def test_body_的_model_最優先(self):
        prefs = preferences(default_models={"claude_cli": "sonnet"})
        with fake_core(prefs):
            self.assertEqual(
                server.resolve_model(1, "claude_cli", "opus"), ("claude_cli", "opus")
            )

    def test_body_沒帶時退回_viewer_偏好(self):
        """前端的「跟隨預設」選項就是不帶 `model`，這是常見路徑不是例外。"""
        prefs = preferences(default_models={"claude_cli": "sonnet"})
        with fake_core(prefs):
            self.assertEqual(
                server.resolve_model(1, "claude_cli", None), ("claude_cli", "sonnet")
            )

    def test_偏好是綁在供應商上的(self):
        """存給 Gemini 的值不可以套到 ClaudeCLI 身上——`opus` 對 Gemini 無意義，
        而錯用的表現是「送出去才壞」，不是當場報錯。"""
        prefs = preferences(default_models={"gemini": "gemini-3.6-flash"})
        with fake_core(prefs, implementation="claude_cli"):
            self.assertEqual(
                server.resolve_model(1, "claude_cli", None), ("claude_cli", None)
            )

    def test_兩層都沒有就回_none(self):
        with fake_core():
            self.assertEqual(
                server.resolve_model(1, "claude_cli", None), ("claude_cli", None)
            )

    def test_偏好存了空字串視為沒設(self):
        prefs = preferences(default_models={"claude_cli": ""})
        with fake_core(prefs):
            self.assertEqual(
                server.resolve_model(1, "claude_cli", None), ("claude_cli", None)
            )

    def test_auto_先展開成具體實作名再查偏好(self):
        """🔴 這條是整個設計的關鍵。

        `resolve()` 拿到「別名 ＋ model」會拒絕，所以別名必須在查偏好**之前**
        就展開；展開後查的是具體那一格（`claude_cli`），不會把 `opus` 錯配給
        Gemini。退而求其次的「auto 時乾脆不查偏好」會讓 Viewer 設了預設卻
        靜默失效，同樣是靜默失敗。
        """
        prefs = preferences(default_models={"claude_cli": "opus"})
        with fake_core(prefs, implementation="claude_cli"):
            self.assertEqual(server.resolve_model(1, "auto", None), ("claude_cli", "opus"))

    def test_auto_加上明確指定的_model_仍要擋下來(self):
        """偏好是 per-provider 的對照表，查哪一格不會錯；body 的單一 model 值
        不帶供應商資訊，「auto ＋ 某個 model」本身就是矛盾的組合。"""
        with fake_core():
            with self.assertRaises(InvalidParameter) as ctx:
                server.resolve_model(1, "auto", "opus")
        self.assertEqual(ctx.exception.http_status, 400)

    def test_沒帶_provider_但_viewer_偏好是_auto_一樣擋(self):
        """這條擋的是「供應商名在兩個地方各解析一次」的漏洞：偏好補上的 auto
        若沒被看見，錯誤就會從 400 掉進串流中的 error 事件裡。"""
        prefs = preferences(default_provider="auto")
        with fake_core(prefs):
            with self.assertRaises(InvalidParameter):
                server.resolve_model(1, None, "opus")

    def test_只有空白的_model_視同沒指定(self):
        """底層供應商自己會 strip，所以 `"   "` 在那裡等同沒指定。這裡若把它當成
        「有指定」，auto 會回 400 而底層根本不認為 Viewer 指定了什麼——同一個輸入
        兩層給出相反結論。非 auto 時的症狀更隱蔽：body 與偏好雙雙不生效還不報錯。"""
        prefs = preferences(default_models={"claude_cli": "opus"})
        with fake_core(prefs):
            self.assertEqual(
                server.resolve_model(1, "claude_cli", "   "), ("claude_cli", "opus")
            )
            # auto ＋ 只有空白 → 不該有 400
            self.assertEqual(server.resolve_model(1, "auto", "  "), ("claude_cli", "opus"))

    def test_供應商不可用時不要把錯誤提前成_400(self):
        """`resolve_name` 拋代表這個供應商現在沒得用（沒裝 CLI、沒設金鑰）。
        那個錯誤要留在它原本會發生的地方（串流中的 error 事件），不要因為
        「順便展開個名字」就變成 400——那會改掉既有端點的錯誤時機。"""
        def boom(name=None):
            raise InvalidParameter("沒有可用的供應商")

        with fake_core(resolve_name=boom):
            self.assertEqual(server.resolve_model(1, "claude", None), ("claude", None))


class GetProviderTests(unittest.TestCase):
    """`get_provider()` 有沒有把解析結果傳給 `providers.resolve()`。"""

    def _spy_resolve(self):
        captured = {}

        def spy(name=None, *, model=None, usage_recorder=None):
            captured["name"] = name
            captured["model"] = model
            return mock.Mock(name="provider")

        return captured, spy

    def test_body_的_model_會傳到底層(self):
        captured, spy = self._spy_resolve()
        with fake_core(), mock.patch.object(server.providers, "resolve", spy):
            server.get_provider(1, "claude_cli", model="opus")
        self.assertEqual(captured["model"], "opus")

    def test_傳給_resolve_的是展開後的具體實作名(self):
        """別名 ＋ model 會被 `resolve()` 拒絕，所以展開要發生在這之前。"""
        captured, spy = self._spy_resolve()
        prefs = preferences(default_models={"claude_cli": "opus"})
        with fake_core(prefs, implementation="claude_cli"), \
                mock.patch.object(server.providers, "resolve", spy):
            server.get_provider(1, "auto")
        self.assertEqual(captured["name"], "claude_cli")
        self.assertEqual(captured["model"], "opus")

    def test_傳下去的是裸值不是識別字(self):
        """前綴由供應商自己加（`claude-cli:opus`）。這裡再加一次就會變成
        `claude-cli:claude-cli:opus` 流進 CLI 參數。"""
        captured, spy = self._spy_resolve()
        prefs = preferences(default_models={"claude_cli": "opus"})
        with fake_core(prefs), mock.patch.object(server.providers, "resolve", spy):
            server.get_provider(1, "claude_cli")
        self.assertEqual(captured["model"], "opus")

    def test_都沒有時傳_none_讓供應商用自己的預設(self):
        captured, spy = self._spy_resolve()
        with fake_core(), mock.patch.object(server.providers, "resolve", spy):
            server.get_provider(1, "claude_cli")
        self.assertIsNone(captured["model"])


class _StopBeforeWork(Exception):
    """探針用：`resolve_model` 被呼叫到就中止，後面的前置工作不必再跑。"""


class StreamEndpointWiringTests(unittest.TestCase):
    """兩個串流端點有沒有把 body 的 `model` 交給那一份共用解析。

    用的是「攔截並中止」的探針：`resolve_model()` 在兩個端點都被安排在
    SSE 開始之前，所以探針一觸發就代表接線正確，也不必替後面的前置工作
    （合併目標、程式碼佐證）準備替身。
    """

    def _spy(self):
        calls = []

        def spy(viewer_id=None, name=None, model=None):
            calls.append({"viewer_id": viewer_id, "name": name, "model": model})
            raise _StopBeforeWork()

        return calls, spy

    def test_summarize_stream(self):
        calls, spy = self._spy()
        req = server.SummarizeRequest(
            space_id="spaces/S", provider="claude_cli", model="opus"
        )
        with fake_core(), \
                mock.patch.object(server, "space_display_name", lambda vid, sid: "測試群"), \
                mock.patch.object(server, "resolve_model", spy):
            with self.assertRaises(_StopBeforeWork):
                server.summarize_stream(req, VIEWER)
        self.assertEqual(calls[0]["model"], "opus")
        self.assertEqual(calls[0]["name"], "claude_cli")

    def test_draft_stream(self):
        calls, spy = self._spy()
        mention = {
            "id": 7,
            "space_id": "spaces/S",
            "message_name": "spaces/S/messages/m1",
            "thread_name": "spaces/S/threads/t1",
        }
        req = server.DraftRequest(provider="claude_cli", model="opus")
        with fake_core(), \
                mock.patch.object(server.repo, "get_mention", lambda vid, mid: dict(mention)), \
                mock.patch.object(server, "resolve_model", spy):
            with self.assertRaises(_StopBeforeWork):
                server.draft_stream(7, req, VIEWER)
        self.assertEqual(calls[0]["model"], "opus")

    def test_worklog_端點刻意沒有_model_欄位(self):
        """工時草稿這一階段**不接 AI**（`meta.model` 固定 null，note 來自
        commit subject）。收下一個保證無效的參數比不收更糟——呼叫端會以為
        自己選的模型生效了。等那裡接上 AI 再一起加。"""
        self.assertNotIn("model", server.WorklogDraftRequest.model_fields)

    def test_兩個端點對_auto_加_model_都回_400(self):
        """同一個錯誤組合在兩個端點要有同一種結果，而且是 HTTP 400——
        串流一旦開始就是 200，之後只能變成 Viewer 比較不會注意到的 error 事件。"""
        mention = {
            "id": 7,
            "space_id": "spaces/S",
            "message_name": "spaces/S/messages/m1",
            "thread_name": "spaces/S/threads/t1",
        }
        with fake_core(), \
                mock.patch.object(server, "space_display_name", lambda vid, sid: "測試群"), \
                mock.patch.object(server.repo, "get_mention", lambda vid, mid: dict(mention)):
            with self.assertRaises(InvalidParameter):
                server.summarize_stream(
                    server.SummarizeRequest(
                        space_id="spaces/S", provider="auto", model="opus"
                    ),
                    VIEWER,
                )
            with self.assertRaises(InvalidParameter):
                server.draft_stream(
                    7, server.DraftRequest(provider="auto", model="opus"), VIEWER
                )


class PatchPreferencesTests(unittest.TestCase):
    """PATCH /api/v1/preferences 的 `default_models`。"""

    @contextmanager
    def _patched(self, existing=None):
        """回傳 `set_default_models` 實際收到的整份對照（沒被呼叫就沒有 `mapping` 鍵）。

        `existing` 給一個例外實例，就是模擬「讀現有偏好時失敗」。
        """
        written = {}
        stored = {} if isinstance(existing, Exception) else dict(existing or {})
        prefs = preferences(default_models=stored)

        def record(viewer_id, mapping):
            written["viewer_id"] = viewer_id
            written["mapping"] = mapping

        def update(viewer_id, **kwargs):
            # 真實的 `update_preferences()` 最後是 `return get_preferences(...)`，
            # 也就是**重讀資料庫**。這個替身照著模擬：回傳值反映的是此刻已經
            # 寫進去的東西，所以「先寫 default_models 再 update」的順序若反了，
            # 回應就會漏掉這次的改動——前端要靠這個回應即時校正畫面。
            return dict(prefs, default_models=written.get("mapping", prefs["default_models"]))

        def current(viewer_id):
            if isinstance(existing, Exception):
                raise existing
            return dict(prefs["default_models"])

        with fake_core(prefs), \
                mock.patch.object(server.repo, "get_default_models", current), \
                mock.patch.object(server.repo, "set_default_models", record), \
                mock.patch.object(server.repo, "update_preferences", update):
            yield written

    def test_別名當鍵要被擋下來(self):
        """`claude`／`auto` 會展開成不同實作，存進去就對應不回來。"""
        for alias in ("claude", "auto"):
            with self.subTest(alias=alias), self._patched() as written:
                req = server.PreferencesRequest(default_models={alias: "opus"})
                with self.assertRaises(InvalidParameter) as ctx:
                    server.patch_preferences(req, VIEWER)
                self.assertEqual(ctx.exception.http_status, 400)
                self.assertNotIn("mapping", written)

    def test_具體實作名可以存(self):
        with self._patched() as written:
            req = server.PreferencesRequest(default_models={"claude_cli": "opus"})
            server.patch_preferences(req, VIEWER)
        self.assertEqual(written["mapping"], {"claude_cli": "opus"})
        self.assertEqual(written["viewer_id"], VIEWER["id"])

    def test_值不驗白名單(self):
        """「也能自己打字自訂」正是這個功能要的，白名單只是常用值。"""
        with self._patched() as written:
            req = server.PreferencesRequest(
                default_models={"claude_cli": "claude-fable-5"}
            )
            server.patch_preferences(req, VIEWER)
        self.assertEqual(written["mapping"], {"claude_cli": "claude-fable-5"})

    def test_只送一個供應商不會清掉另一個(self):
        """前端只會送剛剛改動的那一個。整份覆寫會讓 Viewer 換個供應商調一次
        model，就把另一個供應商存好的偏好弄丟。"""
        with self._patched({"gemini": "gemini-3.6-flash"}) as written:
            req = server.PreferencesRequest(default_models={"claude_cli": "opus"})
            server.patch_preferences(req, VIEWER)
        self.assertEqual(
            written["mapping"],
            {"gemini": "gemini-3.6-flash", "claude_cli": "opus"},
        )

    def test_null_與空字串會清除那一個供應商(self):
        for cleared in (None, "", "   "):
            with self.subTest(cleared=cleared), \
                    self._patched({"claude_cli": "opus", "gemini": "g"}) as written:
                req = server.PreferencesRequest(default_models={"claude_cli": cleared})
                server.patch_preferences(req, VIEWER)
                self.assertEqual(written["mapping"], {"gemini": "g"})

    def test_回應要帶回更新後的_default_models(self):
        """前端靠這個回應即時校正畫面，否則要等下一次 `/me` 才對得上，
        中間有一段狀態不一致。等價於「寫入排在 update_preferences 之前」。"""
        with self._patched({"gemini": "g"}):
            body = server.patch_preferences(
                server.PreferencesRequest(default_models={"claude_cli": "opus"}), VIEWER
            )
        self.assertEqual(body["default_models"], {"gemini": "g", "claude_cli": "opus"})

    def test_前端送整份時三種形態都吃得到(self):
        """前端一律送整份、被清除的那格明寫 null（store/providers.ts:256）。
        設定／清除／不動這三種形態要在同一個請求裡同時成立。"""
        with self._patched({"claude_cli": "opus", "gemini": "g"}) as written:
            req = server.PreferencesRequest(
                default_models={"claude_cli": "sonnet", "gemini": None}
            )
            server.patch_preferences(req, VIEWER)
        self.assertEqual(written["mapping"], {"claude_cli": "sonnet"})

    def test_沒帶這個欄位就完全不動偏好(self):
        with self._patched({"claude_cli": "opus"}) as written:
            server.patch_preferences(server.PreferencesRequest(), VIEWER)
        self.assertNotIn("mapping", written)

    def test_整個欄位送_null_或空物件都不寫入(self):
        """文件寫的是「不改」。照樣寫一次同樣的 JSON 會白白推進 `updated_at`，
        讓「這筆偏好上次什麼時候被動過」變成一個不可信的欄位。"""
        for payload in (None, {}):
            with self.subTest(payload=payload), \
                    self._patched({"claude_cli": "opus"}) as written:
                server.patch_preferences(
                    server.PreferencesRequest(default_models=payload), VIEWER
                )
                self.assertNotIn("mapping", written)

    def test_讀不到現有偏好時不可以靜默覆寫(self):
        """🔴 這條守的是 merge 的**基底**。

        基底若來自會把失敗吞成 `{}` 的 helper，一次短暫的讀取失敗就會讓這支
        PATCH 靜默退化成整份覆寫——另一個供應商那格被清掉、回應還是 200，
        Viewer 沒有任何線索。寫入路徑寧可整個失敗，也不要寫進一份殘缺的資料。
        """
        boom = RuntimeError("DB 掛了")
        with self._patched(boom) as written:
            with self.assertRaises(RuntimeError):
                server.patch_preferences(
                    server.PreferencesRequest(default_models={"claude_cli": "opus"}),
                    VIEWER,
                )
        self.assertNotIn("mapping", written)


class MetaProviderTests(unittest.TestCase):
    """`meta.provider` 要說得出**實際跑的是誰**。

    Viewer 現在會盯著 meta 判斷自己選的 model 有沒有生效，所以 meta 報 claude_cli
    而實際用 gemini 跑，會讓人歸咎到這次的新功能上。
    """

    def test_meta_的_provider_看得到_viewer_偏好(self):
        prefs = preferences(
            default_provider="gemini", default_models={"gemini": "gemini-3.6-flash"}
        )

        class FakeProvider:
            supports_vision = False
            model = "gemini-3.6-flash"

            def stream_text(self, prompt, operation=None, images=None):
                yield "摘要內容"

        class FakeClient:
            def fetch_recent_messages(self, space_id, limit=50):
                return [{"name": "spaces/S/messages/m1", "text": "嗨"}]

        req = server.SummarizeRequest(space_id="spaces/S")
        with fake_core(prefs, implementation="gemini"), \
                mock.patch.object(server, "get_client", lambda vid: FakeClient()), \
                mock.patch.object(server, "get_provider", lambda vid, p, model=None: FakeProvider()), \
                mock.patch.object(server, "space_display_name", lambda vid, sid: "測試群"), \
                mock.patch.object(server, "learn_names", lambda msgs: None), \
                mock.patch.object(server, "learn_dm_peer", lambda v, sid, msgs: None), \
                mock.patch.object(server, "name_resolver_for", lambda v: (lambda uid: "對方")), \
                mock.patch.object(server, "format_conversation", lambda msgs, resolver: "對話"), \
                mock.patch.object(server.repo, "create_summary", lambda *a, **kw: 1):
            resp = server.summarize_stream(req, VIEWER)
            events = [json.loads(chunk.split("data: ", 1)[1])
                      for chunk in asyncio.run(_drain(resp.body_iterator))]

        meta = next(e for e in events if e["type"] == "meta")
        self.assertEqual(meta["provider"], "gemini")
        self.assertEqual(meta["model"], "gemini-3.6-flash")


async def _drain(iterator):
    return [chunk async for chunk in iterator]


class MeEndpointTests(unittest.TestCase):
    def test_ai_帶出_default_models(self):
        """前端要靠這個值決定 ModelSelect 顯示什麼；沒設過的供應商不出現在
        物件裡（前端顯示「跟著供應商預設走」，不是空字串）。"""
        prefs = preferences(default_models={"claude_cli": "opus"})
        with fake_core(prefs), \
                mock.patch.object(server.repo, "credentials_scopes", lambda vid: []), \
                mock.patch.object(server.repo, "get_collector_state", lambda vid: {}), \
                mock.patch.object(server.repo, "count_mentions", lambda vid: {}), \
                mock.patch.object(server.providers, "describe_all", lambda: []), \
                mock.patch.object(server, "_viewer_public", lambda v: dict(v)):
            body = server.get_me(VIEWER)
        self.assertEqual(body["ai"]["default_models"], {"claude_cli": "opus"})


if __name__ == "__main__":
    unittest.main()
