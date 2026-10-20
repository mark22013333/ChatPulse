from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from typing import Awaitable, Callable


class RunState(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureCode(str, Enum):
    AUTH = "auth"
    LEASE = "lease"
    VERSION = "version"
    PROTOCOL = "protocol"
    PROCESS = "process"
    LIMIT = "limit"
    DEADLINE = "deadline"
    EMPTY_RESULT = "empty_result"
    AGENT = "agent"
    CALLBACK = "callback"
    CLEANUP = "cleanup"
    ISOLATION_BLOCKED = "isolation_blocked"


class RuntimeRejected(Exception):
    def __init__(self, code: FailureCode):
        self.code = code
        super().__init__(f"代理執行遭拒絕（{code.value}）")


@dataclass(frozen=True)
class CredentialSnapshot:
    """可信登入入口提供的 binding；本模組不從 CLI 登入推測 Viewer。"""

    viewer_id: str
    external_subject: str
    generation: int
    expires_at: float
    secret: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class SyntheticScope:
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if any(not isinstance(item, str) or not item or len(item) > 128
               for item in self.evidence_ids):
            raise RuntimeRejected(FailureCode.LEASE)
        object.__setattr__(self, "evidence_ids", tuple(sorted(set(self.evidence_ids))))

    @property
    def digest(self) -> str:
        return sha256(json.dumps(self.evidence_ids, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RuntimeLease:
    viewer_id: str
    external_subject: str
    generation: int
    run_id: str
    scope_hash: str
    deadline: float


@dataclass(frozen=True)
class AgentEvent:
    run_id: str
    kind: str
    text: str = ""


EventCallback = Callable[[AgentEvent], Awaitable[None]]


@dataclass(frozen=True)
class CleanupReceipt:
    confirmed: bool
    containment: str


@dataclass(frozen=True)
class Completed:
    run_id: str
    session_id: str
    text: str
    cleanup: CleanupReceipt
    state: RunState = field(default=RunState.COMPLETED, init=False)


@dataclass(frozen=True)
class Failed:
    run_id: str
    code: FailureCode
    cleanup: CleanupReceipt
    state: RunState = field(default=RunState.FAILED, init=False)


@dataclass(frozen=True)
class Cancelled:
    run_id: str
    cleanup: CleanupReceipt
    state: RunState = field(default=RunState.CANCELLED, init=False)


RunOutcome = Completed | Failed | Cancelled


@dataclass(frozen=True)
class RuntimeLimits:
    line_bytes: int = 65536
    stdout_bytes: int = 1048576
    stderr_bytes: int = 262144
    text_chars: int = 131072
    events: int = 4096
    callback_queue: int = 64
    prompt_chars: int = 65536

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise RuntimeRejected(FailureCode.LIMIT)


@dataclass(frozen=True)
class HostFixture:
    """僅供可信合成子程序測試，並非真 adapter 的安全隔離。"""

    command: tuple[str, ...]


@dataclass(frozen=True)
class DockerProbe:
    """離線固定 image 的診斷 launcher，不提供正式 SIWC 或網路出口。"""

    executable: str
    image_id: str
    engine_endpoint: str
    fixture_mode: str = "marker"
    canary_path: str = "/host-chatpulse-canary/secret.txt"
