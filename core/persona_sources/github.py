"""GitHubPersonaSource：從公開 GitHub repository 匯入 persona，並固定版本。

## 兩種輸入形態

1. **repository + persona**（`{"source_type": "github", "repository": "owner/repo",
   "persona": "slug"}`）——結構化，可以先列出有哪些 persona 再選。
2. **url**（`{"source_type": "url", "url": "..."}`）——貼一個網址就匯入。
   只接受 `github.com` 與 `raw.githubusercontent.com`（見 `base.ALLOWED_HOSTS`）。

第 2 種會把 `blob` 網址自動轉成 raw 網址，因為那是使用者最容易貼的形態
（從瀏覽器複製網址列就是 blob）。不轉的話會拿到一整頁 HTML，
而 HTML 餵進正規化器只會得到一堆垃圾條目——所以 `base.safe_get()`
拒絕 `text/html` 並在錯誤訊息裡直接告訴使用者要用 raw 網址。

## 版本固定

`fetch()` 一定先把 ref（分支名或 tag）解析成 **commit SHA**，再用那個 SHA
去取檔案。這不是為了嚴謹好看：

> 今天產生的回話，不能因為遠端 repository 明天偷偷改了 SKILL.md，行為就跟著變。

用分支名取檔案會讓同一個 persona 在不同時間產出不同的 profile，而使用者
完全觀測不到這件事——他只會覺得「這個 persona 最近怪怪的」。所以匯入時
釘住 SHA，之後只有按「更新 Persona」才重新解析一次。

## 不帶任何認證

刻意不讀 `GITHUB_TOKEN`、不帶 Authorization header。公開 repo 不需要，
而 persona 匯入是「使用者提供的網址」——讓那條路徑帶著任何憑證，
就等於給了一個「用我的身分去打某個網址」的原語。未認證的 GitHub API
速率上限是每小時 60 次，對偶發的匯入動作足夠。
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from ..errors import InvalidParameter, PersonaSourceError
from .base import FetchedPersona, PersonaSource, safe_get

_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"

#: `owner/repo` 的合法形態。GitHub 的規則比這寬鬆一點，但收窄到
#: 這個集合可以順便擋掉「把整個網址塞進 repository 欄位」與路徑穿越
#: （`owner/../..`）——後者若進到 raw 網址組裝就會變成任意路徑讀取。
_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,38}/[\w.-]{1,100}$")

#: persona slug 的合法形態。不允許斜線與點，理由同上（路徑穿越）。
_SLUG_RE = re.compile(r"^[\w-]{1,80}$")

#: ref（分支／tag／SHA）的合法形態。
_REF_RE = re.compile(r"^[\w][\w./-]{0,100}$")

#: 一個 persona 的主檔案可能長在哪裡，依序嘗試。
#:
#: **順序有意義，而且第一條不能改成 `<slug>/SKILL.md`。**
#: 2026-09-07 實測 `fxp/persona-distill-skills`：repo 根目錄也有一個
#: `SKILL.md`，但它是「如何蒸餾一個 persona」的方法論，不是 persona。
#: 用寬鬆的 `**/SKILL.md` 或允許空的目錄層級，就會把方法論檔當成一個
#: persona 收進來——而它的內容看起來完全像一份合理的 skill，
#: 使用者要選了之後才發現不對。
_CANDIDATE_PATHS = (
    "personas/{slug}/SKILL.md",
    "personas/{slug}/PERSONA.md",
    "personas/{slug}.md",
    "skills/{slug}/SKILL.md",
    "{slug}/SKILL.md",
)

#: 根目錄的主檔名。**這些刻意不算 persona**（理由見 `_CANDIDATE_PATHS`
#: 上方的註解），列在這裡只為了在「找不到指定的 persona」時能告訴使用者
#: 「這個 repo 是把檔案放根目錄的形態，改用網址模式」——是指路，不是放寬比對。
_ROOT_CANDIDATES: Tuple[str, ...] = ("SKILL.md", "PERSONA.md")

#: 列舉 persona 時用的路徑比對。與 `_CANDIDATE_PATHS` 對應，
#: 但**要求 slug 那一層存在**（`[^/]+`），所以根目錄的 SKILL.md 不會命中。
_LISTING_RE = re.compile(
    r"^(?:personas|skills)/(?P<slug>[^/]+)/(?:SKILL|PERSONA)\.md$"
    r"|^personas/(?P<slug2>[^/]+)\.md$"
)


def _validate_repository(repository: str) -> Tuple[str, str]:
    value = (repository or "").strip().strip("/")
    if not _REPO_RE.match(value):
        raise InvalidParameter(
            f"repository 必須是 owner/repo 的形式，收到 {repository!r}"
        )
    owner, _, repo = value.partition("/")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    # `.` 與 `..` 進到路徑組裝會變成穿越
    for part in (owner, repo):
        if part in (".", "..") or part.startswith("."):
            raise InvalidParameter(f"repository 含不合法的片段：{part!r}")
    return owner, repo


def _validate_slug(slug: str) -> str:
    value = (slug or "").strip().strip("/")
    if not _SLUG_RE.match(value):
        raise InvalidParameter(
            f"persona 名稱只接受英數字、底線與連字號，收到 {slug!r}"
        )
    return value


def _validate_ref(ref: Optional[str]) -> Optional[str]:
    if ref is None:
        return None
    value = str(ref).strip()
    if not value:
        return None
    if not _REF_RE.match(value) or ".." in value:
        raise InvalidParameter(f"ref 含不合法的字元：{ref!r}")
    return value


def _api_json(url: str) -> Any:
    """打 GitHub API 並解析 JSON。"""
    text, _ = safe_get(url, accept="application/vnd.github+json")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise PersonaSourceError(f"GitHub API 回應不是合法 JSON：{exc}") from None


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class GitHubPersonaSource(PersonaSource):
    """公開 GitHub repository 的 persona 來源。"""

    name = "github"
    label = "公開 GitHub Repository"

    # ---------------------------------------------------------------- 列舉

    def _tree_blobs(self, owner: str, repo: str, sha: str) -> Tuple[List[Dict[str, Any]], bool]:
        """整棵樹的 blob 清單 ＋ 「這棵樹有沒有被截斷」。

        抽出來是因為**兩個地方要用**：列舉 persona，以及「找不到指定的
        persona 時告訴使用者這個 repo 其實有哪些」。後者已經解過 commit
        SHA 了，共用這個函式可以不必再解一次（未認證的 GitHub API
        每小時只有 60 次）。

        截斷旗標要一路帶出去：大型 repo 的樹會被截斷，而「這個 repo 有 N 個
        persona」這種斷言在截斷的樹上是錯的（見 `_missing_persona_error`）。
        """
        tree = _api_json(f"{_API}/repos/{owner}/{repo}/git/trees/{sha}?recursive=1")
        if not isinstance(tree, dict):
            raise PersonaSourceError("GitHub trees API 回應格式非預期")
        blobs = [
            entry
            for entry in (tree.get("tree") or [])
            if isinstance(entry, dict) and entry.get("type") == "blob"
        ]
        return blobs, bool(tree.get("truncated"))

    def list_personas(self, *, repository: str, ref: Optional[str] = None, **_: Any) -> List[Dict[str, Any]]:
        """列出這個 repo 有哪些 persona。

        用 trees API 一次拿整棵樹再過濾，而不是逐層打 contents API：
        一次呼叫就好（未認證每小時只有 60 次），而且能同時拿到 blob size。
        """
        owner, repo = _validate_repository(repository)
        sha, resolved_ref = self._resolve_commit(owner, repo, _validate_ref(ref))
        blobs, _truncated = self._tree_blobs(owner, repo, sha)

        seen: Dict[str, Dict[str, Any]] = {}
        for entry in blobs:
            path = str(entry.get("path") or "")
            match = _LISTING_RE.match(path)
            if not match:
                continue
            slug = match.group("slug") or match.group("slug2")
            if not slug or not _SLUG_RE.match(slug):
                continue
            # 同一個 slug 命中多個候選路徑時保留第一個（依 trees 的順序）
            seen.setdefault(
                slug,
                {
                    "id": slug,
                    "name": slug,
                    "path": path,
                    "size": entry.get("size"),
                    "repository": f"{owner}/{repo}",
                    "ref": resolved_ref,
                    "commit_sha": sha,
                },
            )

        return sorted(seen.values(), key=lambda item: item["id"])

    # ---------------------------------------------------------------- 取得

    def fetch(
        self,
        *,
        repository: Optional[str] = None,
        persona: Optional[str] = None,
        url: Optional[str] = None,
        ref: Optional[str] = None,
        **_: Any,
    ) -> FetchedPersona:
        """取回一份 persona。`url` 與 `repository`+`persona` 兩種形態擇一。"""
        if url:
            return self._fetch_by_url(url)
        if not repository or not persona:
            raise InvalidParameter(
                "需要 repository ＋ persona，或是一個 url"
            )
        return self._fetch_by_path(repository, persona, _validate_ref(ref))

    def _resolve_commit(
        self, owner: str, repo: str, ref: Optional[str]
    ) -> Tuple[str, str]:
        """ref（或預設分支）→ `(commit_sha, 實際使用的 ref)`。

        **這是版本固定的關鍵一步。** 之後所有取檔動作都用回傳的 SHA，
        不再用 ref——否則遠端一改，行為就跟著變。
        """
        target = ref
        if not target:
            info = _api_json(f"{_API}/repos/{owner}/{repo}")
            if not isinstance(info, dict):
                raise PersonaSourceError("GitHub repo API 回應格式非預期")
            if info.get("private"):
                raise InvalidParameter("只支援公開 repository")
            target = str(info.get("default_branch") or "main")

        commit = _api_json(f"{_API}/repos/{owner}/{repo}/commits/{target}")
        if not isinstance(commit, dict) or not commit.get("sha"):
            raise PersonaSourceError(f"找不到 {owner}/{repo} 的 {target}")
        return str(commit["sha"]), target

    def _fetch_by_path(
        self, repository: str, persona: str, ref: Optional[str]
    ) -> FetchedPersona:
        owner, repo = _validate_repository(repository)
        slug = _validate_slug(persona)
        sha, resolved_ref = self._resolve_commit(owner, repo, ref)

        attempted: List[str] = []
        for template in _CANDIDATE_PATHS:
            path = template.format(slug=slug)
            attempted.append(path)
            raw_url = f"{_RAW}/{owner}/{repo}/{sha}/{path}"
            try:
                text, final_url = safe_get(raw_url, accept="text/plain")
            except PersonaSourceError as exc:
                # 只有「不存在」才繼續試下一個候選路徑；其他錯誤
                # （轉址過多、超過大小、非 UTF-8）是真的故障，要如實拋出，
                # 不然使用者會收到「找不到」而其實是別的問題。
                if "404" in str(exc):
                    continue
                raise
            return FetchedPersona(
                raw_text=text,
                source_type=self.name,
                name_hint=slug,
                source_repository=f"{owner}/{repo}",
                source_url=final_url,
                source_ref=resolved_ref,
                source_commit_sha=sha,
                source_hash=_content_hash(text),
                extra={"path": path},
            )

        raise self._missing_persona_error(owner, repo, sha, slug, attempted)

    def _missing_persona_error(
        self,
        owner: str,
        repo: str,
        sha: str,
        slug: str,
        attempted: List[str],
    ) -> PersonaSourceError:
        """五條候選路徑都 404 時的錯誤——**要指路，不只是報告失敗**。

        原本的訊息只列了試過的五條路徑。那對「打錯 slug」有幫助，但對真正
        最常見的情況完全沒幫助：**生態裡的多數 repo 是「一個 repo 一個
        persona、SKILL.md 放在根目錄」**（2026-09-11 實測 zeng-shiqiang、
        kaishengwang-perspective、zhuoshu-perspective… 五個都是這種），
        而那種形態在 Repository 模式下**永遠**找不到——根目錄不算 persona
        是刻意的（見 `_LISTING_RE`）。使用者對著五條路徑看不出「該換模式」。

        所以這裡多花一次 trees 呼叫（只在失敗路徑，且共用已解出的 SHA）
        把話講完：這個 repo 有哪些可以匯入，或者它根本是另一種形態、
        該用哪個網址。

        兩個講究：

        * 建議的網址用 **commit SHA 而不是分支名**。實測
          `jangviktor-web/zeng-shiqiang` 的預設分支叫
          `auto-optimize/20260825-…`，**含斜線**——而網址模式是按 `/`
          切段解析的，含斜線的 ref 在那個網址形狀裡結構上表達不出來，
          填了必然 404。SHA 沒有這個問題，而且順便把版本固定住。
        * 只有在樹裡**真的看到**根目錄的 `SKILL.md` 時才建議那個網址，
          不憑猜測給一個會 404 的連結。

        列舉失敗（rate limit、樹格式怪）**不可以蓋掉原本的錯誤**——
        指路是加分，拿不到就退回原本那句。
        """
        base = (
            f"在 {owner}/{repo}@{sha[:7]} 找不到 persona {slug!r}。"
            f"試過的路徑：{'、'.join(attempted)}"
        )
        try:
            blobs, truncated = self._tree_blobs(owner, repo, sha)
        except (PersonaSourceError, InvalidParameter):
            return PersonaSourceError(base)

        slugs: List[str] = []
        root_file: Optional[str] = None
        for entry in blobs:
            path = str(entry.get("path") or "")
            match = _LISTING_RE.match(path)
            if match:
                found = match.group("slug") or match.group("slug2")
                if found and _SLUG_RE.match(found) and found not in slugs:
                    slugs.append(found)
            elif path in _ROOT_CANDIDATES and root_file is None:
                root_file = path

        if slugs:
            shown = "、".join(slugs[:8])
            more = f"（共 {len(slugs)} 個）" if len(slugs) > 8 else ""
            maybe = "（清單可能不完整，這個 repo 的檔案樹太大被截斷了）" if truncated else ""
            # 措辭刻意是「名稱有」而不是「可以匯入的是」。`_LISTING_RE` 只證明
            # **路徑形狀**符合，證明不了那些檔案真的是 persona——實測
            # `anthropics/skills` 有 19 個 `skills/<名稱>/SKILL.md`，全部都不是
            # persona（brand-guidelines、docx 跑淨化都是 is_usable() == False）。
            # 要真的判斷得逐份抓下來跑淨化，19 次請求，不值得。
            # 說「可以匯入」會把人送去撞 409，那比不指路更糟。
            return PersonaSourceError(
                f"{base}。這個 repo 裡名稱形狀符合的有：{shown}{more}{maybe}"
                "（形狀符合不代表它們是 persona——不是的話匯入會被擋下來並說明原因）"
            )

        if root_file:
            return PersonaSourceError(
                f"{base}。這個 repo 沒有 personas/<名稱>/ 這種結構，"
                f"它的 {root_file} 放在根目錄——那種形態要用「網址」模式匯入："
                f"{_RAW}/{owner}/{repo}/{sha}/{root_file}"
            )

        return PersonaSourceError(
            f"{base}。這個 repo 裡找不到任何 persona 檔案"
            "（既沒有 personas/<名稱>/SKILL.md，根目錄也沒有 SKILL.md）。"
        )

    def _fetch_by_url(self, url: str) -> FetchedPersona:
        """從一個 GitHub 網址匯入。

        會盡量從網址反推 owner／repo／ref／path 以便記錄 provenance，
        反推不出來也不擋——那時 provenance 只有 URL 與 content hash。
        content hash 一定有，所以「內容有沒有變」永遠判斷得出來。
        """
        parsed = urlparse(url)

        # scheme 要在這裡先驗，不能只靠 `safe_get()`。
        #
        # 下面把 `github.com/.../blob/...` 重建成 `raw.githubusercontent.com/...`
        # 時用的是 `_RAW` 常數（https），所以使用者貼 `http://github.com/...`
        # 會在重建後變成合法的 https URL——`safe_get()` 的 scheme 檢查就永遠
        # 看不到那個 http。實際連線仍然是 https（不是安全問題），但錯誤訊息
        # 會變成「來源不存在」而不是「只接受 https」，使用者查不出真正的原因。
        if parsed.scheme and parsed.scheme != "https":
            raise InvalidParameter(
                f"persona 來源只接受 https，收到 {parsed.scheme}"
            )

        host = (parsed.hostname or "").lower()
        path_parts = [unquote(p) for p in parsed.path.split("/") if p]

        owner = repo = None
        ref: Optional[str] = None
        file_path: Optional[str] = None
        target = url

        if host == "raw.githubusercontent.com" and len(path_parts) >= 4:
            # /{owner}/{repo}/{ref}/{path...}
            owner, repo, ref = path_parts[0], path_parts[1], path_parts[2]
            file_path = "/".join(path_parts[3:])
        elif host == "github.com" and len(path_parts) >= 5 and path_parts[2] in ("blob", "raw"):
            # /{owner}/{repo}/blob/{ref}/{path...} → 轉成 raw
            owner, repo, ref = path_parts[0], path_parts[1], path_parts[3]
            file_path = "/".join(path_parts[4:])
            target = f"{_RAW}/{owner}/{repo}/{ref}/{file_path}"
        elif host == "github.com" and len(path_parts) >= 5 and path_parts[2] == "tree":
            # 目錄網址：補上主檔名
            owner, repo, ref = path_parts[0], path_parts[1], path_parts[3]
            directory = "/".join(path_parts[4:])
            file_path = f"{directory}/SKILL.md"
            target = f"{_RAW}/{owner}/{repo}/{ref}/{file_path}"
        elif host == "github.com":
            raise InvalidParameter(
                "這個 GitHub 網址看不出是哪個檔案。請貼檔案的 raw 網址"
                "（raw.githubusercontent.com/...），或改用 repository ＋ persona 的方式。"
            )

        text, final_url = safe_get(target, accept="text/plain")

        # 若 ref 是分支名（不是 SHA），額外解析一次真正的 commit SHA，
        # 好讓 provenance 可重現。解析失敗不擋匯入——content hash 已經
        # 能回答「內容有沒有變」，SHA 是加分。
        commit_sha: Optional[str] = None
        if owner and repo and ref:
            if re.fullmatch(r"[0-9a-f]{40}", ref):
                commit_sha = ref
            else:
                try:
                    commit_sha, _ = self._resolve_commit(owner, repo, _validate_ref(ref))
                except (InvalidParameter, PersonaSourceError):
                    commit_sha = None

        name_hint = ""
        if file_path:
            segments = [p for p in file_path.split("/") if p]
            if len(segments) >= 2:
                name_hint = segments[-2]
            elif segments:
                name_hint = segments[-1].rsplit(".", 1)[0]

        return FetchedPersona(
            raw_text=text,
            source_type="url",
            name_hint=name_hint,
            source_repository=f"{owner}/{repo}" if owner and repo else None,
            source_url=final_url,
            source_ref=ref,
            source_commit_sha=commit_sha,
            source_hash=_content_hash(text),
            extra={"path": file_path} if file_path else {},
        )
