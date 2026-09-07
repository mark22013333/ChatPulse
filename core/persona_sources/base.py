"""PersonaSource：從外部取得 persona 原文的抽象，以及取得時的安全邊界。

## 為什麼要抽象這一層

第一個支援的來源是一個 GitHub repository，但把 persona 系統綁死在那個
repository 上會有兩個問題：它是個 1 star、單一維護者、兩個 persona 的
個人專案（2026-09-07 實測），而且「公開人物風格蒸餾」這件事將來一定會有
別的來源（自架的 registry、團隊自己的風格檔、使用者手動填寫）。

所以這一層只定義一件事：**「給我一個識別，回我一份原文與它的 provenance」**。
淨化與正規化不在這裡（那是 `core/personas.py`），資料庫也不在這裡
（那是 `core/repository.py`）。

## 安全邊界為什麼放在這一層

因為這是唯一會對外發網路請求的地方。`fetch()` 的實作**必須**走
`safe_get()`，它負責：

* 只允許 HTTPS
* 只允許 allowlist 內的網域
* 拒絕私有網段與 loopback（含 DNS 解析後的實際 IP，防 DNS rebinding）
* 不自動跟隨轉址（自己處理，每一跳重新驗證，且有次數上限）
* 回應大小上限（邊下載邊累計，不是下載完才檢查）
* content-type 檢查
* timeout

這些**不是**為了防止取到惡意的 persona 內容——那由 `core/personas.py`
的四層淨化負責。它們是為了防止「ChatPulse 變成一台可以被指使去打任意
內部網址的代理」：persona 匯入的 URL 由使用者提供，而 ChatPulse 跑在
使用者的機器上、帶著他的 Google OAuth token。一個能讓它去 GET
`http://169.254.169.254/` 或 `http://127.0.0.1:8000/api/v1/me` 的功能，
比一個會產出奇怪語氣的 persona 危險得多。
"""

import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from ..errors import InvalidParameter, PersonaSourceError

#: 允許連線的網域。**allowlist，不是 blocklist。**
#:
#: MVP 只支援 GitHub：需求明確排除「第一版就做任意網站 crawler」，
#: 而收窄到兩個已知網域讓 SSRF 的風險面積小到幾乎沒有。
#: 要加新來源就在這裡加一行，並在 ADR 記錄為什麼那個網域可以信任。
ALLOWED_HOSTS = (
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
)

#: 單一檔案的大小上限。
#:
#: 實測的 persona 檔案是 21–23 KB，方法論檔 16 KB。512 KB 給了 20 倍餘裕，
#: 同時擋掉「指向一個 100 MB 的檔案把記憶體吃光」。
MAX_RESPONSE_BYTES = 512 * 1024

#: 下載時每次讀多少。小一點才能在超過上限時**及早**中斷，
#: 而不是把整個 body 收完才發現太大。
_CHUNK_BYTES = 16 * 1024

#: 連線與讀取的 timeout（秒）。分開設定，因為「連不上」與「連上但不吐資料」
#: 是兩種不同的故障，後者需要更長的容忍。
TIMEOUT = (5, 15)

#: 轉址上限。GitHub 的 raw 網址有時會轉一次，兩跳足夠。
MAX_REDIRECTS = 2

#: 可接受的 content-type 前綴。
#:
#: GitHub raw 對 `.md` 回 `text/plain`；API 回 `application/json`。
#: 不接受 `text/html`——拿到 HTML 通常代表「這是一個頁面，不是檔案」
#: （例如把 `github.com/owner/repo/blob/...` 當成 raw 網址用），
#: 那時候把整頁 HTML 餵進正規化器只會得到一堆垃圾條目。
ALLOWED_CONTENT_TYPES = (
    "text/plain",
    "text/markdown",
    "application/json",
    "application/vnd.github",
    "text/x-markdown",
)

_USER_AGENT = "ChatPulse-PersonaImport/1.0 (+https://github.com/)"


def _reject_private_address(host: str) -> None:
    """解析 host 並確認每一個 IP 都是公開位址。

    **解析後才檢查**是關鍵：`ALLOWED_HOSTS` 擋掉了明目張膽的
    `http://127.0.0.1`，但擋不掉「一個 allowlist 內的網域被解析到內網 IP」
    這種情況（DNS rebinding，或單純是被 hosts 檔／內部 DNS 指走了）。

    檢查**所有**回傳的位址而不只是第一個：一個 host 可以同時有
    公開的 A 記錄與私有的 AAAA 記錄，只驗第一個就會漏。
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise PersonaSourceError(f"無法解析網域 {host}：{exc}") from None

    if not infos:
        raise PersonaSourceError(f"網域 {host} 沒有解析到任何位址")

    for info in infos:
        raw = info[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            raise PersonaSourceError(f"網域 {host} 解析到無法判讀的位址 {raw!r}") from None
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise PersonaSourceError(
                f"網域 {host} 解析到非公開位址 {address}，已拒絕連線"
            )


def _validate_url(url: str) -> str:
    """檢查 URL 的 scheme 與網域；回傳正規化後的 host。"""
    parsed = urlparse(url or "")
    if parsed.scheme != "https":
        raise InvalidParameter(f"persona 來源只接受 https，收到 {parsed.scheme or '(空)'}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise InvalidParameter("persona 來源網址沒有網域")
    if host not in ALLOWED_HOSTS:
        raise InvalidParameter(
            f"persona 來源只接受 {'、'.join(ALLOWED_HOSTS)}，收到 {host}"
        )
    if parsed.port not in (None, 443):
        raise InvalidParameter(f"persona 來源不接受自訂連接埠（收到 {parsed.port}）")
    return host


def safe_get(url: str, *, accept: Optional[str] = None) -> Tuple[str, str]:
    """在安全邊界內取得一個 URL 的內容。

    回傳 `(內容文字, 最終網址)`。最終網址要回傳，因為轉址之後
    provenance 記的應該是實際取到內容的那個位址。

    每一次轉址都重新跑完整驗證（scheme／allowlist／IP），
    否則「第一跳是 github.com、第二跳轉去內網」就繞過了全部檢查。
    """
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        host = _validate_url(current)
        _reject_private_address(host)

        headers = {"User-Agent": _USER_AGENT}
        if accept:
            headers["Accept"] = accept

        try:
            response = requests.get(
                current,
                headers=headers,
                timeout=TIMEOUT,
                # 自己處理轉址，才能逐跳驗證
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise PersonaSourceError(f"取得 persona 來源失敗：{exc}") from None

        with response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                if not location:
                    raise PersonaSourceError("來源回了轉址但沒有 Location")
                if hop >= MAX_REDIRECTS:
                    raise PersonaSourceError(f"轉址次數超過上限（{MAX_REDIRECTS}）")
                current = requests.compat.urljoin(current, location)
                continue

            if response.status_code == 404:
                raise PersonaSourceError(f"來源不存在（404）：{current}")
            if response.status_code == 403:
                raise PersonaSourceError(
                    "來源拒絕存取（403）。若是 GitHub API 的速率限制，稍後再試。"
                )
            if response.status_code != 200:
                raise PersonaSourceError(
                    f"來源回應 HTTP {response.status_code}：{current}"
                )

            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type and not content_type.startswith(ALLOWED_CONTENT_TYPES):
                raise PersonaSourceError(
                    f"來源的內容類型是 {content_type}，只接受 "
                    f"{'、'.join(ALLOWED_CONTENT_TYPES)}。"
                    "若你貼的是 GitHub 網頁網址，請改用 raw 檔案網址。"
                )

            # 先看 Content-Length 能不能早退，但**不信任它**——
            # 它可以是錯的或不存在，真正的把關是下面邊讀邊累計。
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise PersonaSourceError(
                    f"來源檔案 {int(declared)} bytes，超過上限 {MAX_RESPONSE_BYTES}"
                )

            buffer = bytearray()
            for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                if not chunk:
                    continue
                buffer.extend(chunk)
                if len(buffer) > MAX_RESPONSE_BYTES:
                    raise PersonaSourceError(
                        f"來源檔案超過上限 {MAX_RESPONSE_BYTES} bytes，已中斷下載"
                    )

            try:
                return buffer.decode("utf-8"), current
            except UnicodeDecodeError:
                raise PersonaSourceError("來源檔案不是 UTF-8 文字") from None

    raise PersonaSourceError("轉址處理異常結束")


@dataclass(frozen=True)
class FetchedPersona:
    """從來源取回的一份 persona：原文 ＋ 可重現的 provenance。

    **provenance 不是稽核用的裝飾，是功能的一部分。** 沒有 commit SHA
    就沒有辦法保證「今天產生的草稿明天還是同樣的行為」——遠端隨時可以
    改 SKILL.md，而使用者不會知道。所以匯入時固定版本、只有按「更新」
    才重新取得（見 `core/repository.py` 的 personas 表與 ADR-0007）。
    """

    #: persona 原文（markdown）。**只供 debug 與更新時比較差異，
    #: 永遠不得作為 generation system instruction。**
    raw_text: str
    source_type: str
    name_hint: str = ""
    description_hint: str = ""
    source_repository: Optional[str] = None
    source_url: Optional[str] = None
    source_ref: Optional[str] = None
    source_commit_sha: Optional[str] = None
    #: 內容本身的雜湊。commit SHA 說的是「repo 當時在哪個版本」，
    #: content hash 說的是「我們實際讀到的那份檔案內容」——
    #: 兩者都要，因為同一個 commit 下不同路徑的檔案不一樣，
    #: 而「更新後內容有沒有真的變」要靠 content hash 才判斷得出來。
    source_hash: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class PersonaSource(ABC):
    """persona 來源的抽象。

    刻意只有兩個方法：列出有什麼、取一份回來。沒有 `search()`、沒有
    `sync_all()`——那些是 marketplace 的功能，需求明確把它排在 MVP 之外。
    """

    #: 程式識別字，也是 API `source_type` 欄位接受的值
    name: str = "base"
    label: str = "未命名來源"

    @abstractmethod
    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """列出這個來源有哪些 persona 可以匯入。

        回傳 `[{"id": ..., "name": ..., "description": ...}]`。
        來源不支援列舉時回空陣列（不要拋錯——「不能列」與「壞了」不同）。
        """

    @abstractmethod
    def fetch(self, **kwargs: Any) -> FetchedPersona:
        """取回一份 persona 的原文與 provenance。"""
