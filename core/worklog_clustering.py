"""把一天的證據分成 N 筆工時（Draft Worklog 的分群，ADR-0008）。

`core/worklog_evidence.py` 回答的是「這天做了什麼」，產出一串平鋪的
`Evidence`；時數分配（`core/worklog_allocation.py`）要的卻是「這天分成幾件
事、各佔多少比重」。中間這一步就是分群。

**為什麼另開一個模組而不是塞進 `worklog_evidence`。** 那個模組的 docstring
第一句就寫著「這裡不做判斷，只做蒐集」，而分群**就是判斷**——同樣一批
commit，按 issue key 分、按 repo 分、按時間切段，會得到完全不同的草稿。
把兩件事放在同一個檔案裡，那條界線一個月後就沒有人守得住了。另一個現實
理由：蒐集那側要跑 subprocess 與 HTTP，分群是純資料轉換，分開之後這裡的
測試連 git 都不需要（該檔已經 1000 行以上，也不該再長）。

**規則由強到弱，先命中先歸群**（呼叫端不得自行調換順序）：

  1. commit message 或 Chat 訊息裡出現 **issue key**（`PEI-133`、
     `FIA01P2403-391`）→ 同一個 key 歸同一群，群的 `label` 就是那個 key。
     **這是最強的訊號**，因為那是人自己寫在 commit 裡的，不是系統猜的；
     它也因此會**跨 repo**——同一張單在前後端各改一半是常態。
  2. 其餘 commit 按 **repo** 歸群，`label` 用 repo 名。
  3. Chat 證據按 **space** 歸群，`label` 用 Space 名。
  4. 每群的 `weight` ＝ 群內所有證據 `weight_input` 的總和。
  5. 每群保留 `evidence_refs`，指回 SSE `meta` 裡的 `commits[]`／`chat[]`。

**每一群都要能回答「為什麼這些被分在一起」**，那句話就是 `reason`，前端會
直接顯示。這不是註解也不是除錯訊息：分群是啟發式，猜錯時使用者唯一能察覺
的方式就是讀那句話發現「這兩件事根本無關」——所以它必須是人話、必須提到
實際的 key／repo／Space 名，而不是「依規則 1 分群」這種對誰都沒用的句子。

**分群不決定專案歸屬。** 群的 `label` 是 `PEI-133` 也不代表這筆工時就填在
`PEI-133`，它只是拿去撈候選 issue 的關鍵字；選擇權永遠在 Viewer
（ADR-0008 的唯一硬規則）。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import config as cfg
from .worklog_evidence import Evidence, find_issue_keys, is_standards_reference


#: 群的種類。順序即強度，也決定顯示順序（強訊號排前面）。
KIND_ISSUE = "issue"
KIND_REPO = "repo"
KIND_SPACE = "space"

_KIND_RANK = {KIND_ISSUE: 0, KIND_REPO: 1, KIND_SPACE: 2}


def issue_keys_of(evidence: Evidence) -> List[str]:
    """這筆證據提到的 issue key（已濾掉 ADR／UTF 那類標準編號）。

    從 `terms` 與 `title` 兩邊找：`terms` 是從**完整內文**抽的（Chat 的
    `title` 只是 80 字節錄，長訊息的單號會被截掉），`title` 則涵蓋
    「terms 因為數量上限被擠掉」的邊角情況。

    「哪些看起來像但其實不是」的判準在 `worklog_evidence.is_standards_reference`
    ——2026-09-13 這張黑名單原本放在本模組，只擋得住 `label`：`ADR-0008` 照樣
    是 `terms[0]`，於是 `query_term` 還是把它送去 ZPlanner 當 `q`，正是黑名單
    本來要防的後果。判準搬到抽詞那側之後才是同一份定義管兩件事。
    """
    keys = find_issue_keys(" ".join(evidence.terms), evidence.title)
    return [k for k in keys if not is_standards_reference(k)]


@dataclass(frozen=True)
class Cluster:
    """一群證據 ＝ 草稿裡的一筆工時。

    `key` 是內部識別（`issue:PEI-133`／`repo:cs-backend`／`space:spaces/AAA`），
    只用來歸群與排序去重；給人看的是 `label`。兩者分開是因為 label 會重複
    （兩個 repo 可以同名、Space 名更是常常撞），而 key 必須唯一。
    """

    key: str
    kind: str  # issue | repo | space
    label: str
    reason: str  # 為什麼這些被分在一起（前端直接顯示的可見文字）
    weight: float
    evidence_refs: Tuple[str, ...]
    terms: Tuple[str, ...]
    occurred_at: str  # 群內最早的一筆。分配演算法的 tie-break 第二層吃這個
    issue_key: Optional[str] = None  # 只有 kind == issue 才有值
    commit_count: int = 0
    chat_count: int = 0
    source_labels: Tuple[str, ...] = ()  # 這群橫跨了哪些 repo／Space

    @property
    def size(self) -> int:
        return len(self.evidence_refs)

    @property
    def query_term(self) -> str:
        """拿去 `list_worklogable_issues(q=…)` 的關鍵字。

        **一定要有值**：那支 API 的 `q` 必填，空字串回 0 筆而不是全部
        （見 `core/zplanner_client.py`），空手去查會得到「我沒有任何可填
        工時的 issue」這個完全錯誤的結論。所以抽不到關鍵字時退回 `label`
        （repo 名或 Space 名）——查不準好過不查。

        **跳過標準編號**（`ADR-0008`、`UTF-8`）：拿它去查，候選清單必然是空的
        ——那正是「有候選卻查不到」與「真的沒有候選」長得一模一樣的情況。
        `extract_terms` 已經把這種詞排到最後，這裡再擋一次是因為 `Evidence`
        的 `terms` 可以由呼叫端自行組（測試與未來的第三種來源都會這麼做），
        而這是送出查詢前的最後一道。
        """
        for term in self.terms:
            if not is_standards_reference(term):
                return term
        return self.label

    def to_meta(self) -> Dict[str, Any]:
        """SSE `meta.clusters[]` 的形狀（計畫 §5）。

        `candidates` 不在這裡：候選 issue 要打 ZPlanner 才問得到，那是
        API 層的事，由呼叫端把它併進這個 dict。
        """
        return {
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "reason": self.reason,
            "weight": round(self.weight, 4),
            "evidence_refs": list(self.evidence_refs),
            "terms": list(self.terms),
            "occurred_at": self.occurred_at,
            "issue_key": self.issue_key,
            "commit_count": self.commit_count,
            "chat_count": self.chat_count,
            "source_labels": list(self.source_labels),
        }

    def to_allocation_item(self) -> Dict[str, Any]:
        """餵給 `worklog_allocation.allocate()` 的形狀。

        回 dict 而不是 `AllocationItem`，是為了不讓這個模組 import 分配演算法
        ——兩者互不依賴才各自測得起來（`allocate` 的 `_coerce_items` 本來就
        吃 Mapping）。

        `issue_key` 只在 issue 群有值，而且它在分配裡**只是 tie-break 的最後
        一層**，不是「這筆要填在這張單」——歸屬仍然由 Viewer 選。
        """
        return {
            "weight": self.weight,
            "occurred_at": self.occurred_at,
            "issue_key": self.issue_key,
        }


@dataclass
class _Bucket:
    """歸群過程中的暫存體。刻意可變。"""

    key: str
    kind: str
    label: str
    issue_key: Optional[str]
    items: List[Evidence]


def _composition(commit_count: int, chat_count: int) -> str:
    """「3 個 commit 與 2 則發言」。兩種來源都要講得出來。"""
    parts: List[str] = []
    if commit_count:
        parts.append(f"{commit_count} 個 commit")
    if chat_count:
        parts.append(f"{chat_count} 則發言")
    return " 與 ".join(parts) or "0 筆證據"


def _reason(bucket: _Bucket, commit_count: int, chat_count: int, sources: Sequence[str]) -> str:
    """產生「為什麼這些被分在一起」那句話。

    寫成具體的句子而不是規則編號：使用者要讀的是理由，不是我們的實作。
    """
    # 尾端固定留一個空白：`commit`／`PEI-133` 是拉丁字，緊貼著中文的
    # 「都」「在」會黏成一團（「11 個 commit都在」）。
    what = _composition(commit_count, chat_count) + " "
    plural = "都" if (commit_count + chat_count) > 1 else ""

    if bucket.kind == KIND_ISSUE:
        text = f"{what}{plural}提到 {bucket.label}"
        if len(sources) > 1:
            # 這一句是規則 1 勝過規則 2 的現場證明：同一張單在兩個 repo
            # 各改一半是常態，只按 repo 分會把它切成兩筆無關的工時。
            text += f"（跨 {'、'.join(sources)}）"
        return text
    if bucket.kind == KIND_REPO:
        return f"{what}{plural}在 {bucket.label}，commit 訊息沒有提到 issue key"
    return f"{what}{plural}在「{bucket.label}」"


def _collect_terms(items: Sequence[Evidence], *, limit: int, first: Optional[str] = None) -> List[str]:
    """彙整這群的關鍵字：保留出現順序、去重、套上限。

    `first` 是要釘在第一個的詞（issue 群就是那個 key）——`query_term` 取的是
    第一個，而對一張已知的單來說，沒有任何關鍵字比單號本身更準。
    """
    out: List[str] = []
    seen = set()

    def add(token: str) -> None:
        text = (token or "").strip()
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        out.append(text)

    if first:
        add(first)
    for item in items:
        for term in item.terms:
            add(term)
    return out[:limit]


def build_clusters(
    evidence: Sequence[Evidence], *, max_terms: Optional[int] = None
) -> List[Cluster]:
    """把一天的證據分群。沒有證據就回空清單（**不是**一個空群）。

    空清單是呼叫端發 SSE `NO_EVIDENCE` 的依據；回一個空群會讓那天看起來
    「有一件事，只是沒有內容」，然後被分掉一整天的時數。

    輸出順序是決定性的：種類（issue → repo → space）→ 權重大者 → 時間早者
    → key 字典序。**決定性不是美觀問題**，同一天重新產一次草稿卻換了順序，
    使用者沒有辦法判斷哪一份才對（理由同 `worklog_allocation` 的 tie-break）。
    """
    limit = max_terms or cfg.WORKLOG_MAX_TERMS
    buckets: Dict[str, _Bucket] = {}

    for item in evidence or ():
        # 規則 1：issue key（最強訊號，見 `issue_keys_of`）
        keys = issue_keys_of(item)
        if keys:
            # 一筆證據同時提到兩張單時只歸第一張：分到兩群會讓它的權重
            # 被算兩遍，那天的時數就會憑空多出來一塊。
            issue_key = keys[0]
            bucket_key = f"issue:{issue_key}"
            kind, label = KIND_ISSUE, issue_key
        elif item.kind == "commit":
            # 規則 2
            issue_key = None
            label = item.source_label or "(未命名 repo)"
            bucket_key = f"repo:{label}"
            kind = KIND_REPO
        else:
            # 規則 3。用 space_id 當 key 而不是名字：兩個 Space 同名是常態，
            # 合在一起會讓證據欄指到別的聊天室。
            issue_key = None
            label = item.source_label or item.space_id or "(未命名聊天室)"
            bucket_key = f"space:{item.space_id or label}"
            kind = KIND_SPACE

        bucket = buckets.get(bucket_key)
        if bucket is None:
            bucket = _Bucket(
                key=bucket_key, kind=kind, label=label, issue_key=issue_key, items=[]
            )
            buckets[bucket_key] = bucket
        bucket.items.append(item)

    clusters: List[Cluster] = []
    for bucket in buckets.values():
        commit_count = sum(1 for e in bucket.items if e.kind == "commit")
        chat_count = len(bucket.items) - commit_count
        sources: List[str] = []
        for e in bucket.items:
            if e.source_label and e.source_label not in sources:
                sources.append(e.source_label)
        clusters.append(
            Cluster(
                key=bucket.key,
                kind=bucket.kind,
                label=bucket.label,
                reason=_reason(bucket, commit_count, chat_count, sources),
                # 規則 4：群的權重是成員的總和。不取平均——一整天都在同一張
                # 單上做的十個 commit，本來就該比另一件事的單一 commit 重。
                weight=sum(e.weight_input for e in bucket.items),
                # 規則 5：只留 ref，不留內容（與 mentions 表同一條鐵律）
                evidence_refs=tuple(e.ref for e in bucket.items),
                terms=tuple(
                    _collect_terms(bucket.items, limit=limit, first=bucket.issue_key)
                ),
                occurred_at=min(e.occurred_at for e in bucket.items),
                issue_key=bucket.issue_key,
                commit_count=commit_count,
                chat_count=chat_count,
                source_labels=tuple(sources),
            )
        )

    clusters.sort(key=lambda c: (_KIND_RANK[c.kind], -c.weight, c.occurred_at, c.key))
    return clusters
