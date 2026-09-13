"""證據分群（`core/worklog_clustering.py`）。

分群是 Draft Worklog 裡**唯一沒有被規格寫死**的一段，所以這套測試守的是
那四條規則的**相對強度**，不是某一次的輸出長相：

  1. issue key 勝過 repo——同一張單在前後端各改一半是常態，只按 repo 分
     會把它切成兩筆看起來無關的工時。
  2. 沒有 issue key 的 commit 才按 repo 分。
  3. Chat 按 Space 分。
  4. 權重是成員的總和，而且**每一筆證據只能進一群**（進兩群等於那天的
     時數憑空多出一塊）。

另外守「每群都講得出為什麼」：`reason` 必須提到實際的 key／repo／Space 名。
那句話會直接顯示給使用者，是他察覺「這兩件事根本無關」的唯一管道。

執行：.venv/bin/python -m unittest discover -s tests/unit -p 'test_worklog_clustering*.py'
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import worklog_clustering as wc  # noqa: E402
from core.worklog_evidence import Evidence, extract_terms, find_issue_keys  # noqa: E402


def commit(
    sha: str,
    subject: str,
    *,
    repo: str = "cs-backend",
    at: str = "2026-09-13T10:00:00+08:00",
    weight: float = 1.0,
) -> Evidence:
    """一筆 commit 證據。`terms` 走真的抽詞器，不是手寫——分群吃的就是它。"""
    return Evidence(
        kind="commit",
        ref=f"commit:{sha}",
        occurred_at=at,
        title=subject,
        source_label=repo,
        terms=tuple(extract_terms(subject)),
        weight_input=weight,
        sha=sha,
        short_sha=sha[:7],
        author_email="me@corp.com",
    )


def chat(
    mid: str,
    text: str,
    *,
    space_id: str = "spaces/AAA",
    space_name: str = "客服專案群",
    at: str = "2026-09-13T11:00:00+08:00",
    weight: float = 1.0,
) -> Evidence:
    return Evidence(
        kind="chat",
        ref=f"chat:{space_id}/messages/{mid}",
        occurred_at=at,
        title=text[:80],
        source_label=space_name,
        terms=tuple(extract_terms(text)),
        weight_input=weight,
        space_id=space_id,
        message_name=f"{space_id}/messages/{mid}",
        char_count=len(text),
    )


class TestIssueKeyBeatsRepo(unittest.TestCase):
    """規則 1：issue key 是最強訊號，而且會跨 repo。"""

    def test_same_key_across_repos_is_one_cluster(self):
        clusters = wc.build_clusters(
            [
                commit("a" * 40, "fix(export): 修正 PEI-133 的匯出逾時", repo="cs-backend"),
                commit("b" * 40, "feat: PEI-133 前端加上重試按鈕", repo="cs-frontend"),
            ]
        )
        self.assertEqual(len(clusters), 1, "同一個 key 跨 repo 仍然是一群")
        self.assertEqual(clusters[0].label, "PEI-133")
        self.assertEqual(clusters[0].kind, wc.KIND_ISSUE)
        self.assertEqual(clusters[0].issue_key, "PEI-133")
        # 理由要講得出「跨了哪兩個 repo」——那正是它勝過規則 2 的現場證明
        self.assertIn("PEI-133", clusters[0].reason)
        self.assertIn("cs-backend", clusters[0].reason)
        self.assertIn("cs-frontend", clusters[0].reason)
        self.assertEqual(clusters[0].source_labels, ("cs-backend", "cs-frontend"))

    def test_different_keys_are_different_clusters(self):
        clusters = wc.build_clusters(
            [
                commit("a" * 40, "PEI-133 匯出", repo="cs-backend"),
                commit("b" * 40, "PEI-077 後台", repo="cs-backend"),
            ]
        )
        self.assertEqual({c.label for c in clusters}, {"PEI-133", "PEI-077"})

    def test_key_wins_even_when_repo_is_the_same(self):
        """同一個 repo 裡，有 key 的與沒 key 的要分開——不是全部塞進 repo 群。"""
        clusters = wc.build_clusters(
            [
                commit("a" * 40, "PEI-133 匯出", repo="cs-backend"),
                commit("b" * 40, "整理設定檔", repo="cs-backend"),
            ]
        )
        self.assertEqual(len(clusters), 2)
        kinds = {c.kind: c.label for c in clusters}
        self.assertEqual(kinds[wc.KIND_ISSUE], "PEI-133")
        self.assertEqual(kinds[wc.KIND_REPO], "cs-backend")

    def test_chat_joins_the_same_issue_cluster_as_commits(self):
        """發言提到同一張單，就和 commit 同一群——兩種來源不各自為政。"""
        clusters = wc.build_clusters(
            [
                commit("a" * 40, "PEI-133 匯出"),
                chat("m1", "PEI-133 那支我已經改好了，等等上 UAT"),
            ]
        )
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].commit_count, 1)
        self.assertEqual(clusters[0].chat_count, 1)
        self.assertIn("1 個 commit 與 1 則發言", clusters[0].reason)

    def test_long_issue_key_shape(self):
        """`FIA01P2403-391` 這種長 key 也要認得（規格 §1 明列的形狀）。"""
        clusters = wc.build_clusters([commit("a" * 40, "FIA01P2403-391 調整報表")])
        self.assertEqual(clusters[0].label, "FIA01P2403-391")

    def test_key_found_in_long_chat_text_beyond_the_excerpt(self):
        """Chat 的 `title` 只是 80 字節錄，長訊息的單號要靠 `terms` 才找得到。"""
        text = "今天先把匯出那條路徑的逾時看完，" * 6 + "這張是 PEI-901"
        evidence = chat("m1", text)
        self.assertNotIn("PEI-901", evidence.title, "前置條件：節錄要真的截掉了單號")
        self.assertEqual(wc.build_clusters([evidence])[0].label, "PEI-901")

    def test_evidence_mentioning_two_keys_counts_once(self):
        """一筆提到兩張單只歸第一張。分到兩群會讓它的權重被算兩遍。"""
        clusters = wc.build_clusters(
            [commit("a" * 40, "PEI-133 與 PEI-077 一起修", weight=2.0)]
        )
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].label, "PEI-133")
        self.assertEqual(sum(c.weight for c in clusters), 2.0)


class TestFalseIssueKeys(unittest.TestCase):
    """長得像單號、但不是單號的東西不可以自成一群。

    這些不是想像出來的案例：2026-09-13 拿本 repo 自己的 commit 跑分群時，
    `docs(zplanner): 補 ADR-0008 …` 真的被分成一個 label 是 `ADR-0008` 的群，
    而那個 label 會被當成最強訊號拿去 ZPlanner 撈候選。
    """

    def test_adr_reference_is_not_an_issue(self):
        clusters = wc.build_clusters(
            [commit("a" * 40, "docs(zplanner): 補 ADR-0008 並修正三處記述", repo="chatpulse")]
        )
        self.assertEqual(clusters[0].kind, wc.KIND_REPO)
        self.assertEqual(clusters[0].label, "chatpulse")

    def test_standards_are_not_issues(self):
        for subject in (
            "fix: 修正 UTF-8 編碼",
            "chore: 時間一律用 ISO-8601",
            "fix: 換成 RFC-3339 解析",
            "chore: 改用 SHA-256",
            "fix(security): 處理 CVE-2024-1234",
        ):
            with self.subTest(subject=subject):
                clusters = wc.build_clusters([commit("a" * 40, subject)])
                self.assertEqual(clusters[0].kind, wc.KIND_REPO, subject)

    def test_real_keys_still_win(self):
        """濾假單號不可以把真的一起濾掉。"""
        clusters = wc.build_clusters(
            [commit("a" * 40, "fix: PEI-133 照 ADR-0008 改用 UTF-8")]
        )
        self.assertEqual(clusters[0].label, "PEI-133")

    def test_helper_is_where_the_judgement_lives(self):
        """`find_issue_keys` 保持純粹（形狀的唯一定義），過濾在分群這一層。"""
        self.assertEqual(find_issue_keys("ADR-0008"), ["ADR-0008"])
        self.assertEqual(wc.issue_keys_of(commit("a" * 40, "ADR-0008")), [])


class TestRepoAndSpaceGrouping(unittest.TestCase):
    def test_commits_without_key_group_by_repo(self):
        clusters = wc.build_clusters(
            [
                commit("a" * 40, "整理設定檔", repo="cs-backend"),
                commit("b" * 40, "補上測試", repo="cs-backend"),
                commit("c" * 40, "調整版面", repo="chatpulse"),
            ]
        )
        self.assertEqual(len(clusters), 2)
        by_label = {c.label: c for c in clusters}
        self.assertEqual(by_label["cs-backend"].size, 2)
        self.assertEqual(by_label["chatpulse"].size, 1)
        self.assertIn("2 個 commit", by_label["cs-backend"].reason)
        self.assertIn("cs-backend", by_label["cs-backend"].reason)
        self.assertIn("issue key", by_label["cs-backend"].reason, "要說出為什麼不是按單分")

    def test_chat_groups_by_space(self):
        clusters = wc.build_clusters(
            [
                chat("m1", "早上先處理匯出的事", space_id="spaces/AAA", space_name="客服專案群"),
                chat("m2", "下午跟 PM 對規格", space_id="spaces/AAA", space_name="客服專案群"),
                chat("m3", "另一個群的討論", space_id="spaces/BBB", space_name="平台維運"),
            ]
        )
        self.assertEqual(len(clusters), 2)
        by_label = {c.label: c for c in clusters}
        self.assertEqual(by_label["客服專案群"].size, 2)
        self.assertEqual(by_label["客服專案群"].kind, wc.KIND_SPACE)
        self.assertIn("平台維運", by_label["平台維運"].reason)
        self.assertNotIn("客服專案群", by_label["平台維運"].reason, "理由不能提到別群的來源")

    def test_same_space_name_different_ids_stay_apart(self):
        """兩個 Space 同名是常態。合在一起會讓證據欄指到別的聊天室。"""
        clusters = wc.build_clusters(
            [
                chat("m1", "第一個群", space_id="spaces/AAA", space_name="專案群"),
                chat("m2", "第二個群", space_id="spaces/BBB", space_name="專案群"),
            ]
        )
        self.assertEqual(len(clusters), 2)
        self.assertEqual({c.key for c in clusters}, {"space:spaces/AAA", "space:spaces/BBB"})

    def test_pure_chat_day_produces_only_space_clusters(self):
        clusters = wc.build_clusters([chat("m1", "整天都在開會與對規格")])
        self.assertEqual([c.kind for c in clusters], [wc.KIND_SPACE])
        self.assertEqual(clusters[0].commit_count, 0)
        self.assertEqual(clusters[0].chat_count, 1)


class TestWeightsAndRefs(unittest.TestCase):
    def test_weight_is_the_sum_of_members(self):
        clusters = wc.build_clusters(
            [
                commit("a" * 40, "PEI-133 一", weight=1.5),
                commit("b" * 40, "PEI-133 二", weight=2.25),
            ]
        )
        self.assertAlmostEqual(clusters[0].weight, 3.75)

    def test_every_evidence_lands_in_exactly_one_cluster(self):
        """總權重守恆。漏一筆是少算工時，重複一筆是憑空多算。"""
        evidence = [
            commit("a" * 40, "PEI-133 匯出", weight=1.0),
            commit("b" * 40, "整理設定", repo="chatpulse", weight=2.0),
            chat("m1", "跟 PM 對規格", weight=3.0),
        ]
        clusters = wc.build_clusters(evidence)
        self.assertAlmostEqual(sum(c.weight for c in clusters), 6.0)
        refs = [r for c in clusters for r in c.evidence_refs]
        self.assertEqual(sorted(refs), sorted(e.ref for e in evidence))
        self.assertEqual(len(refs), len(set(refs)), "同一個 ref 不可以出現在兩群")

    def test_refs_point_back_to_meta_shapes(self):
        clusters = wc.build_clusters([commit("a" * 40, "PEI-133"), chat("m1", "PEI-133 好了")])
        self.assertEqual(
            list(clusters[0].evidence_refs),
            ["commit:" + "a" * 40, "chat:spaces/AAA/messages/m1"],
        )

    def test_occurred_at_is_the_earliest_member(self):
        clusters = wc.build_clusters(
            [
                commit("a" * 40, "PEI-133 晚的", at="2026-09-13T18:00:00+08:00"),
                commit("b" * 40, "PEI-133 早的", at="2026-09-13T09:30:00+08:00"),
            ]
        )
        self.assertEqual(clusters[0].occurred_at, "2026-09-13T09:30:00+08:00")


class TestQueryTerm(unittest.TestCase):
    """`list_worklogable_issues(q=…)` 的 `q` **必填**，空字串回 0 筆而不是全部。"""

    def test_issue_cluster_queries_the_key(self):
        clusters = wc.build_clusters([commit("a" * 40, "fix: PEI-133 匯出逾時")])
        self.assertEqual(clusters[0].query_term, "PEI-133")
        self.assertEqual(clusters[0].terms[0], "PEI-133", "單號要釘在第一個")

    def test_never_empty_even_when_nothing_extractable(self):
        clusters = wc.build_clusters([commit("a" * 40, "wip")])
        self.assertTrue(clusters[0].query_term.strip())
        self.assertEqual(clusters[0].query_term, "cs-backend", "抽不到關鍵字就退回 repo 名")

    def test_space_cluster_falls_back_to_space_name(self):
        clusters = wc.build_clusters([chat("m1", "ok", space_name="平台維運")])
        self.assertTrue(clusters[0].query_term.strip())


class TestDeterminismAndShape(unittest.TestCase):
    def test_empty_evidence_returns_no_clusters(self):
        """空清單，**不是**一個空群——空群會被分掉一整天的時數。"""
        self.assertEqual(wc.build_clusters([]), [])
        self.assertEqual(wc.build_clusters(()), [])

    def test_same_input_gives_the_same_answer(self):
        evidence = [
            commit("a" * 40, "PEI-133 匯出", weight=1.0),
            commit("b" * 40, "整理設定", repo="chatpulse", weight=1.0),
            chat("m1", "對規格", weight=1.0),
        ]
        first = [c.key for c in wc.build_clusters(evidence)]
        for _ in range(20):
            self.assertEqual([c.key for c in wc.build_clusters(evidence)], first)

    def test_input_order_does_not_change_the_grouping(self):
        evidence = [
            commit("a" * 40, "PEI-133 匯出", weight=1.0),
            commit("b" * 40, "PEI-133 前端", repo="cs-frontend", weight=1.0),
            commit("c" * 40, "整理設定", repo="chatpulse", weight=5.0),
        ]
        expected = {(c.key, c.weight) for c in wc.build_clusters(evidence)}
        self.assertEqual({(c.key, c.weight) for c in wc.build_clusters(evidence[::-1])}, expected)

    def test_stronger_kinds_sort_first(self):
        clusters = wc.build_clusters(
            [
                chat("m1", "閒聊", weight=9.0),
                commit("a" * 40, "整理設定", repo="chatpulse", weight=8.0),
                commit("b" * 40, "PEI-133", weight=0.1),
            ]
        )
        self.assertEqual([c.kind for c in clusters], [wc.KIND_ISSUE, wc.KIND_REPO, wc.KIND_SPACE])

    def test_to_meta_shape(self):
        cluster = wc.build_clusters([commit("a" * 40, "fix: PEI-133 匯出")])[0]
        meta = cluster.to_meta()
        for key in (
            "key", "kind", "label", "reason", "weight", "evidence_refs",
            "terms", "occurred_at", "issue_key", "commit_count", "chat_count",
        ):
            self.assertIn(key, meta)
        self.assertIsInstance(meta["evidence_refs"], list)
        self.assertNotIn("candidates", meta, "候選是 API 層併上去的，不是分群的產物")

    def test_to_allocation_item_shape(self):
        cluster = wc.build_clusters([commit("a" * 40, "fix: PEI-133", weight=2.0)])[0]
        item = cluster.to_allocation_item()
        self.assertEqual(item["weight"], 2.0)
        self.assertEqual(item["issue_key"], "PEI-133")
        self.assertTrue(item["occurred_at"])

    def test_repo_cluster_has_no_issue_key(self):
        """`issue_key` 在分配裡只是 tie-break，不是「這筆填在這張單」。"""
        cluster = wc.build_clusters([commit("a" * 40, "整理設定")])[0]
        self.assertIsNone(cluster.issue_key)
        self.assertIsNone(cluster.to_allocation_item()["issue_key"])


class TestIssueKeyHelper(unittest.TestCase):
    """`find_issue_keys` 住在 `worklog_evidence`，是那個形狀的唯一定義。"""

    def test_order_and_dedupe(self):
        self.assertEqual(
            find_issue_keys("PEI-133 與 PEI-077", "又是 PEI-133"),
            ["PEI-133", "PEI-077"],
        )

    def test_ignores_lowercase_and_plain_numbers(self):
        self.assertEqual(find_issue_keys("pei-133 版本 1-2 修了 3 個"), [])

    def test_handles_empty(self):
        self.assertEqual(find_issue_keys("", None), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
