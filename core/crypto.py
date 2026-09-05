"""OAuth token 的本機加密（缺陷 D-5 的 Phase 2 處置）。

規格對應：SPECIFICATION.md 九節設計要點——
「credentials.encrypted_token 以本機金鑰加密。金鑰不與資料庫同檔存放。」

金鑰檔預設落在 data/token.key（權限 0600），與 data/chatpulse.db 同目錄但不同檔案。
要更嚴格可用 CHATPULSE_KEY_FILE 指到別的位置（例如外接磁碟或 Keychain 匯出的檔案）。
"""

import os
import stat
from typing import Dict

from cryptography.fernet import Fernet, InvalidToken

from . import config as cfg
from .errors import ConfigurationError

_fernet: Fernet | None = None


def _load_or_create_key() -> bytes:
    """讀取金鑰；不存在則產生一把並以 0600 寫檔。"""
    path = cfg.ENCRYPTION_KEY_FILE
    if os.path.exists(path):
        with open(path, "rb") as f:
            key = f.read().strip()
        if not key:
            raise ConfigurationError(f"加密金鑰檔存在但內容為空：{path}")
        return key

    cfg.ensure_data_dir()
    key = Fernet.generate_key()
    # 先以 0600 建檔再寫入，避免金鑰有任何一瞬間是全域可讀的
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """加密後回傳可直接存進 TEXT 欄位的字串。"""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """解密；金鑰不符或內容被改動時拋 ConfigurationError。"""
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ConfigurationError(
            "無法解密已儲存的憑證——金鑰檔可能被更換或刪除。"
            f"請刪除 {cfg.DB_PATH} 的 credentials 資料後重新登入。"
        ) from exc


def key_file_mode() -> str:
    """回傳金鑰檔的權限字串，供健檢與測試斷言用。"""
    if not os.path.exists(cfg.ENCRYPTION_KEY_FILE):
        return "missing"
    return stat.filemode(os.stat(cfg.ENCRYPTION_KEY_FILE).st_mode)


def harden_file(path: str) -> bool:
    """把含密文的檔案權限收成 0600。回傳 True 表示有實際調整過。

    為什麼需要這個：Phase 2 的憑證存在加密的 `credentials` 表裡，但
    `config/google_chat_token.json`（Phase 1 之前的單人 token）仍以**明文**
    保留在檔案系統上供 bootstrap 匯入。`google-auth` 與 `InstalledAppFlow`
    寫這個檔時用的是預設 umask，實測是 **0644**——同一台機器上任何其他
    使用者帳號都讀得到裡面的 `refresh_token`，而 refresh token 不會隨
    access token 過期而失效，等於直接交出這個 Google 帳號的 Chat 讀寫授權。

    所以凡是讀寫這類檔案的路徑，都要順手呼叫這個函式。
    """
    if not os.path.exists(path):
        return False
    if os.name == "nt":
        # Windows 沒有 POSIX 的 group/other 權限位元：st_mode 恆為 0o666，
        # chmod 只映射到唯讀旗標。照 POSIX 邏輯跑的話，`current & 0o077`
        # 永遠為真、每次都回報「已收緊」——**日誌會謊報這個檔案被保護了**。
        # 寧可誠實回報「沒做」，也不要留一個假的安全保證。
        return False
    try:
        current = stat.S_IMODE(os.stat(path).st_mode)
        if current & 0o077:  # group 或 other 有任何權限
            os.chmod(path, 0o600)
            return True
    except OSError:
        pass
    return False


def harden_credential_files() -> Dict[str, str]:
    """收斂所有已知的憑證檔與資料檔權限，回傳收斂後的權限字串。"""
    targets = [
        cfg.LEGACY_TOKEN_FILE,
        cfg.CLIENT_SECRET_FILE,
        cfg.ENCRYPTION_KEY_FILE,
        cfg.DB_PATH,
    ]
    result: Dict[str, str] = {}
    for path in targets:
        harden_file(path)
        result[path] = (
            stat.filemode(os.stat(path).st_mode) if os.path.exists(path) else "missing"
        )
    # config/ 目錄本身也收：裡面全是憑證
    for directory in (cfg.CONFIG_DIR, cfg.DATA_DIR):
        if os.path.isdir(directory):
            try:
                if stat.S_IMODE(os.stat(directory).st_mode) & 0o077:
                    os.chmod(directory, 0o700)
            except OSError:
                pass
            result[directory] = stat.filemode(os.stat(directory).st_mode)
    return result
