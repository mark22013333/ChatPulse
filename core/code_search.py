"""參考專案原始碼檢索（ADR-0006）。

Draft Reply 的第三種脈絡來源：被 @ 的討論串、Reference Space，以及這裡的原始碼。

**為什麼不做 RAG**：與 ADR-0003 同一條理由——你已經知道答案在哪個專案、
哪個環境。這裡沒有 embedding、沒有向量庫、沒有索引要同步，就是對使用者
指定的分支跑 `git grep`，而且搜了哪些詞、命中哪些檔案都回顯給使用者看。

**為什麼絕不 checkout**：使用者的工作區隨時可能有未提交的修改、或正在 rebase。
本模組一律用 `git grep <sha>` 與 `git show <sha>:<path>` 直接讀 object database，
構造上就不可能碰到 working tree 或 index。

**這個模組刻意不 import repository**：呼叫端把解析好的專案資料傳進來，
讓這裡是一層純 git + 文字處理，不接資料庫也能測。
"""

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import config as cfg
from .errors import (
    CodeBranchNotFound,
    CodeProjectUnavailable,
    ConfigurationError,
)


# --------------------------------------------------------------------------
# 資料結構
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeHit:
    path: str  # repo 相對路徑。絕對路徑永遠不進 prompt（會洩漏開發機目錄結構）
    start_line: int  # 1-based
    end_line: int
    text: str
    matched_terms: Tuple[str, ...]


@dataclass(frozen=True)
class CodeContext:
    """一個專案在一個環境下的檢索結果。"""

    project_name: str
    environment: str
    branch: str
    commit_sha: str  # 短 SHA，讓工程師能自己 git show 驗證模型看到什麼
    commit_date: str
    terms: Tuple[str, ...]
    hits: Tuple[CodeHit, ...]
    truncated: bool
    notes: Tuple[str, ...]


def validate_environment(value: Optional[str]) -> str:
    """比照 prompts.validate_style：非法值直接擋，不做寬容解析。"""
    env = (value or cfg.CODE_ENV_DEFAULT).strip().lower()
    if env not in cfg.CODE_ENVIRONMENTS:
        raise ValueError(
            f"environment 必須是 {'／'.join(cfg.CODE_ENVIRONMENTS)} 其中之一，收到 {value!r}"
        )
    return env


def environment_label(environment: str) -> str:
    return cfg.CODE_ENV_LABELS.get(environment, environment)


# --------------------------------------------------------------------------
# git 呼叫
# --------------------------------------------------------------------------


def _git(repo_path: str, *args: str, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    """跑一個唯讀的 git 指令。

    永遠傳 argv list，不組 shell 字串——repo_path 與分支名都是使用者輸入。

    兩個環境變數是必要的，不是保險：
      * GIT_OPTIONAL_LOCKS=0：不去搶 index.lock，否則使用者自己在跑
        `git add` 的那一刻我們的查詢會失敗（或反過來干擾他）。
      * GIT_TERMINAL_PROMPT=0：任何憑證提示都會把 SSE 串流卡死到 timeout。
    """
    env = dict(os.environ)
    env.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C.UTF-8",
        }
    )
    try:
        return subprocess.run(
            [cfg.CODE_GIT_BIN, "--no-pager", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or cfg.CODE_GIT_TIMEOUT,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"找不到 git 執行檔（{cfg.CODE_GIT_BIN}）。"
            "請安裝 git，或用 CHATPULSE_GIT_BIN 指定完整路徑。"
        ) from exc


def _ensure_repo(repo_path: str, project_name: str) -> None:
    if not repo_path or not os.path.isdir(repo_path):
        raise CodeProjectUnavailable(
            f"專案「{project_name}」的路徑不存在或不是資料夾。"
            "請到設定頁的參考專案更新路徑。"
        )
    proc = _git(repo_path, "rev-parse", "--git-dir")
    if proc.returncode != 0:
        raise CodeProjectUnavailable(
            f"專案「{project_name}」的路徑不是 git repo。請到設定頁更新路徑。"
        )


def _list_branches(repo_path: str, limit: int = 20) -> List[str]:
    proc = _git(repo_path, "branch", "--format=%(refname:short)", "--all")
    if proc.returncode != 0:
        return []
    names = [b.strip() for b in proc.stdout.splitlines() if b.strip()]
    return names[:limit]


def _resolve_commit(repo_path: str, branch: str, project_name: str) -> Tuple[str, str]:
    """把分支釘成 commit SHA。

    一定要先釘 SHA 再做後續查詢：否則中途一個 `git fetch` 會讓分支移動，
    草稿引用的行號就對不上它自己宣稱的 commit——那是最難察覺的一種錯。
    """
    proc = _git(repo_path, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
    if proc.returncode != 0 or not proc.stdout.strip():
        available = _list_branches(repo_path)
        hint = ("；現有分支：" + "、".join(available)) if available else ""
        raise CodeBranchNotFound(
            f"專案「{project_name}」對應的分支 {branch!r} 不存在{hint}"
        )
    sha = proc.stdout.strip()
    date_proc = _git(repo_path, "log", "-1", "--format=%cI", sha)
    commit_date = date_proc.stdout.strip() if date_proc.returncode == 0 else ""
    return sha[:7], commit_date


def _repo_state_notes(repo_path: str, branch: str, sha: str, commit_date: str) -> List[str]:
    """收集使用者必須知道的狀態差異。

    這些不是錯誤，但不講會讓草稿誤導人——特別是工作區有未提交變更那條：
    工程師看到草稿描述的行為跟他眼前的檔案不一樣，會以為草稿錯了。
    """
    notes: List[str] = []

    dirty = _git(repo_path, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode == 0 and dirty.stdout.strip():
        notes.append(
            f"本機 working tree 有未提交變更，但以下程式碼讀自 {branch}@{sha}，不含這些變更。"
        )

    git_dir_proc = _git(repo_path, "rev-parse", "--git-dir")
    if git_dir_proc.returncode == 0:
        git_dir = git_dir_proc.stdout.strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(repo_path, git_dir)
        for marker, label in (
            ("rebase-merge", "rebase"),
            ("rebase-apply", "rebase"),
            ("MERGE_HEAD", "merge"),
            ("CHERRY_PICK_HEAD", "cherry-pick"),
        ):
            if os.path.exists(os.path.join(git_dir, marker)):
                notes.append(
                    f"這個 repo 正在進行 {label}，分支狀態可能是暫時的（讀取本身仍然有效）。"
                )
                break

    if commit_date:
        try:
            dt = datetime.fromisoformat(commit_date)
            age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days
            if age > cfg.CODE_STALE_BRANCH_DAYS:
                notes.append(
                    f"{branch} 最後一次提交在 {commit_date[:10]}（{age} 天前），"
                    "若近期有部署請先 git fetch。"
                )
        except ValueError:
            pass

    return notes


# --------------------------------------------------------------------------
# 關鍵字抽取
# --------------------------------------------------------------------------

#: 會命中半個 repo 的泛用詞。扣掉它們比多抓幾個詞重要得多——
#: 一個 `error` 命中 300 個檔案，等於整個預算被一個沒有資訊量的詞吃光。
_STOPWORDS = frozenset(
    """
    data value result error status handler service config test user list update
    name type item index count total object string number method class function
    return import export default async await const true false null none self this
    the and for with from that this what why how when where which
    """.split()
)

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_BACKTICK_RE = re.compile(r"`([^`\n]{2,80})`")
_FILENAME_RE = re.compile(
    r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|java|kt|go|rs|rb|php|cs|sql|ya?ml|json|sh|vue)\b",
    re.IGNORECASE,
)
_DOTTED_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")


def _looks_like_code(token: str) -> bool:
    """只收散文裡不會出現的形狀：snake_case／camelCase／PascalCase／CONSTANT_CASE。"""
    if "_" in token:
        return True
    if token.isupper() and len(token) >= 3:
        return True
    # 有大小寫轉折（camelCase / PascalCase）
    if any(c.isupper() for c in token[1:]) and any(c.islower() for c in token):
        return True
    return False


def _specificity(token: str) -> Tuple[int, int]:
    transitions = sum(
        1 for a, b in zip(token, token[1:]) if a.islower() and b.isupper()
    ) + token.count("_")
    return (transitions, len(token))


def extract_search_terms(
    mention_text: str, thread_text: str = "", *, max_terms: Optional[int] = None
) -> List[str]:
    """從問題文字抽出看起來像識別字的 token。

    **這是整個功能最弱的一環，而且是刻意做弱的。** 改成讓 LLM 規劃查詢會多一次
    模型呼叫、不可重現，而且會把「AI 猜答案在哪」這個 ADR-0003 明確拒絕的性質
    帶回來。補償手段是透明：搜了哪些詞會回顯給使用者，猜錯了他一眼就看得到，
    而且可以用 code_terms 覆寫、或直接指定 explicit_paths。

    要升級成 LLM 抽詞，先寫新的 ADR。
    """
    limit = max_terms or cfg.CODE_MAX_TERMS
    # 被 @ 的那句話權重最高，討論串只當補充
    primary = mention_text or ""
    secondary = thread_text or ""

    ordered: List[str] = []
    seen = set()

    def add(tok: str) -> None:
        tok = tok.strip().strip(".,;:!?()[]{}\"'")
        if not tok or tok.lower() in seen or tok.lower() in _STOPWORDS:
            return
        seen.add(tok.lower())
        ordered.append(tok)

    # 1. 反引號內的內容：使用者明確標記成程式碼，優先度最高
    for src in (primary, secondary):
        for m in _BACKTICK_RE.finditer(src):
            add(m.group(1))

    # 2. 檔名
    for src in (primary, secondary):
        for m in _FILENAME_RE.finditer(src):
            add(m.group(0))

    # 3. 帶點的路徑（module.function）
    for src in (primary, secondary):
        for m in _DOTTED_RE.finditer(src):
            add(m.group(0))

    # 4. 識別字形狀的 token，依特異度排序
    candidates: List[str] = []
    for src in (primary, secondary):
        for m in _IDENT_RE.finditer(src):
            tok = m.group(0)
            if tok.lower() in _STOPWORDS or tok.lower() in seen:
                continue
            if _looks_like_code(tok):
                candidates.append(tok)
    for tok in sorted(set(candidates), key=_specificity, reverse=True):
        add(tok)

    return ordered[:limit]


# --------------------------------------------------------------------------
# 機敏遮蔽
# --------------------------------------------------------------------------

_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|pwd|token|authorization|private[_-]?key)"
    r"(\s*[:=]\s*)([\"']?)([^\s\"'`,;]{8,})\3"
)
_SECRET_PREFIX_RE = re.compile(
    r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-[A-Za-z0-9-]{8,})\b"
)
_REDACTED = "«已遮蔽»"


def _is_secret_path(path: str) -> bool:
    name = path.replace("\\", "/")
    base = name.rsplit("/", 1)[-1]
    for pattern in cfg.CODE_SECRET_PATH_PATTERNS:
        pat = pattern.replace("**/", "")
        if fnmatch.fnmatch(base.lower(), pat.lower()) or fnmatch.fnmatch(
            name.lower(), pattern.lower()
        ):
            return True
    return False


def redact(text: str) -> str:
    """遮蔽看起來像憑證的字串。

    這是 best-effort，不是保證。真正的防線是 CODE_SECRET_PATH_PATTERNS
    （整份檔案跳過）與使用者自己的 exclude_globs。
    """
    text = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    text = _SECRET_PREFIX_RE.sub(_REDACTED, text)
    return text


# --------------------------------------------------------------------------
# 檢索
# --------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """中英混排的保守估計。

    ASCII 原始碼實際接近 1/4，中文註解接近 1/1；取 1/3 寧可高估，
    高估只是少送幾段，低估會超出預算。
    """
    return max(1, len(text) // 3)


def _pathspec(include_globs: Sequence[str], exclude_globs: Sequence[str]) -> List[str]:
    spec: List[str] = list(include_globs) if include_globs else []
    spec.extend(exclude_globs or cfg.CODE_DEFAULT_EXCLUDE_GLOBS)
    return spec


def _grep(
    repo_path: str, sha: str, terms: Sequence[str], pathspec: Sequence[str]
) -> Dict[str, List[Tuple[int, str]]]:
    """對一個 commit 搜尋所有關鍵字，回傳 {path: [(line_no, term), ...]}。

    所有關鍵字用重複的 -e 一次送出（一個 process，不是 N 個）——
    git grep 的 -e 之間是 OR。
    """
    if not terms:
        return {}
    args = ["grep", "-n", "-I", "--fixed-strings", "--ignore-case", "--no-color"]
    for t in terms:
        args += ["-e", t]
    args.append(sha)
    if pathspec:
        args.append("--")
        args.extend(pathspec)

    proc = _git(repo_path, *args)
    # exit code 1 是「沒有命中」，不是錯誤。搞反是這裡的經典 bug。
    if proc.returncode not in (0, 1):
        return {}

    found: Dict[str, List[Tuple[int, str]]] = {}
    prefix = sha + ":"
    for line in proc.stdout.splitlines():
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix) :]
        try:
            path, lineno_s, content = rest.split(":", 2)
            lineno = int(lineno_s)
        except ValueError:
            continue
        low = content.lower()
        matched = next((t for t in terms if t.lower() in low), terms[0])
        found.setdefault(path, []).append((lineno, matched))
    return found


def _read_file(repo_path: str, sha: str, path: str) -> Optional[str]:
    proc = _git(repo_path, "show", f"{sha}:{path}")
    if proc.returncode != 0:
        return None
    return proc.stdout


def _merge_windows(lines: List[int], context: int, total: int) -> List[Tuple[int, int]]:
    """把鄰近的命中行合併成一個區間，避免同一段程式碼被切成好幾塊重複送。"""
    windows: List[Tuple[int, int]] = []
    for ln in sorted(set(lines)):
        start = max(1, ln - context)
        end = min(total, ln + context)
        if windows and start <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


_DEF_RE = re.compile(
    r"^\s*(def |class |function |func |public |private |protected |CREATE TABLE|CREATE OR REPLACE)",
    re.IGNORECASE,
)


def _rank_key(hit: CodeHit, explicit: bool) -> Tuple:
    """排序在套預算之前，讓截斷砍掉的是最差的命中而不是隨機的尾巴。"""
    is_test = 1 if re.search(r"(^|/)(tests?|__tests__|spec)/", hit.path) else 0
    has_def = 1 if _DEF_RE.search(hit.text) else 0
    depth = hit.path.count("/")
    return (
        0 if explicit else 1,  # 明確指定的檔案永遠最前面
        -len(hit.matched_terms),  # 命中越多不同關鍵字越相關
        -has_def,  # 定義優於單純使用
        is_test,  # 非測試優先
        depth,  # 路徑淺者優先
        hit.path,
    )


def collect(
    *,
    repo_path: str,
    project_name: str,
    environment: str,
    branch: str,
    terms: Sequence[str],
    include_globs: Sequence[str] = (),
    exclude_globs: Sequence[str] = (),
    budget_tokens: Optional[int] = None,
    explicit_paths: Sequence[str] = (),
) -> Tuple[CodeContext, List[str]]:
    """主入口。回傳 (context, skipped)。

    形狀刻意與 attachments.collect 一致：局部失敗降級成 skipped 訊息，
    不讓整份草稿掛掉。但「路徑不存在」與「分支不存在」是例外——那兩個會拋，
    因為使用者要的就是有依據的草稿，靜默給一份沒依據的更糟。
    """
    skipped: List[str] = []
    budget = budget_tokens or cfg.CODE_BUDGET_TOKENS_DRAFT

    _ensure_repo(repo_path, project_name)
    sha, commit_date = _resolve_commit(repo_path, branch, project_name)
    notes = _repo_state_notes(repo_path, branch, sha, commit_date)

    pathspec = _pathspec(include_globs, exclude_globs)
    raw_hits: List[Tuple[CodeHit, bool]] = []

    # 第一層：使用者明確指定的檔案，零猜測
    for path in explicit_paths:
        if _is_secret_path(path):
            skipped.append(f"{path}：路徑符合機敏樣式，未讀取")
            continue
        content = _read_file(repo_path, sha, path)
        if content is None:
            skipped.append(f"{path}：在 {branch}@{sha} 中不存在")
            continue
        lines = content.splitlines()
        text = "\n".join(f"{i:>5} | {l}" for i, l in enumerate(lines[:400], 1))
        raw_hits.append(
            (
                CodeHit(
                    path=path,
                    start_line=1,
                    end_line=min(len(lines), 400),
                    text=redact(text),
                    matched_terms=tuple(terms),
                ),
                True,
            )
        )

    # 第二層：關鍵字搜尋
    try:
        found = _grep(repo_path, sha, terms, pathspec)
    except subprocess.TimeoutExpired:
        skipped.append(f"搜尋逾時（{cfg.CODE_GIT_TIMEOUT} 秒），只回傳已取得的片段")
        found = {}

    already_reported = {
        s.split("：", 1)[0] for s in skipped
    }  # explicit_paths 已經回報過的，不要再講一次
    for path, entries in found.items():
        if any(h.path == path for h, _ in raw_hits):
            continue  # 已經整檔讀過
        if _is_secret_path(path):
            if path not in already_reported:
                skipped.append(f"{path}：路徑符合機敏樣式，未讀取")
            continue
        content = _read_file(repo_path, sha, path)
        if content is None:
            continue
        if len(content.encode("utf-8", "ignore")) > cfg.CODE_MAX_FILE_BYTES:
            skipped.append(f"{path}：檔案過大，未讀取")
            continue
        lines = content.splitlines()
        matched_terms = tuple(sorted({t for _, t in entries}))
        for start, end in _merge_windows(
            [ln for ln, _ in entries], cfg.CODE_CONTEXT_LINES, len(lines)
        ):
            body = "\n".join(
                f"{i:>5} | {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1)
            )
            raw_hits.append(
                (
                    CodeHit(
                        path=path,
                        start_line=start,
                        end_line=min(end, len(lines)),
                        text=redact(body),
                        matched_terms=matched_terms,
                    ),
                    False,
                )
            )

    raw_hits.sort(key=lambda pair: _rank_key(pair[0], pair[1]))

    kept: List[CodeHit] = []
    used = 0
    truncated = False
    for hit, _explicit in raw_hits:
        if len(kept) >= cfg.CODE_MAX_HITS_PER_PROJECT:
            truncated = True
            break
        cost = estimate_tokens(hit.text)
        if used + cost > budget:
            truncated = True
            continue
        kept.append(hit)
        used += cost

    dropped = len(raw_hits) - len(kept)
    if truncated and dropped > 0:
        notes.append(f"因為長度限制，另有 {dropped} 段相符的程式碼沒有列出。")

    ctx = CodeContext(
        project_name=project_name,
        environment=environment,
        branch=branch,
        commit_sha=sha,
        commit_date=commit_date,
        terms=tuple(terms),
        hits=tuple(kept),
        truncated=truncated,
        notes=tuple(notes),
    )
    return ctx, skipped


def verify_project(repo_path: str, branches: Dict[str, str]) -> Dict[str, Any]:
    """登錄／更新專案時呼叫，確認路徑與每個分支都還在。

    在登錄的當下就大聲失敗，遠比等到產草稿時才發現便宜——後者的成本是
    一份「本來以為有依據」的草稿。
    """
    result: Dict[str, Any] = {
        "repo_ok": False,
        "branches": {},
        "working_tree_dirty": False,
        "error": None,
    }
    if not repo_path or not os.path.isdir(repo_path):
        result["error"] = "路徑不存在或不是資料夾"
        return result
    proc = _git(repo_path, "rev-parse", "--git-dir")
    if proc.returncode != 0:
        result["error"] = "這個路徑不是 git repo"
        return result
    result["repo_ok"] = True

    dirty = _git(repo_path, "status", "--porcelain", "--untracked-files=no")
    result["working_tree_dirty"] = bool(dirty.returncode == 0 and dirty.stdout.strip())

    available = _list_branches(repo_path)
    for env, branch in (branches or {}).items():
        info: Dict[str, Any] = {"branch": branch, "exists": False}
        rp = _git(repo_path, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
        if rp.returncode == 0 and rp.stdout.strip():
            sha = rp.stdout.strip()
            info["exists"] = True
            info["commit"] = sha[:7]
            dp = _git(repo_path, "log", "-1", "--format=%cI", sha)
            info["commit_date"] = dp.stdout.strip() if dp.returncode == 0 else ""
        else:
            info["error"] = "分支不存在"
            near = [b for b in available if branch and branch.lower() in b.lower()]
            if near:
                info["did_you_mean"] = near[:5]
        result["branches"][env] = info
    return result
