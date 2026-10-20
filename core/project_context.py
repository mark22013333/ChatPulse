import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

MAX_SOURCES = 12


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def git_fingerprint(project: dict) -> str:
    return digest({key: project[key] for key in (
        "repo_path", "branches", "default_env", "include_globs", "exclude_globs", "enabled"
    )})


@dataclass(frozen=True)
class ChatBinding:
    id: str
    label: str
    space_id: str
    kind: Literal["chat"] = "chat"
    category: Literal["space", "pm"] = "space"


@dataclass(frozen=True)
class GitBinding:
    id: str
    label: str
    code_project_id: int
    environment: str
    paths: tuple[str, ...]
    kind: Literal["git"] = "git"


SourceBinding = ChatBinding | GitBinding


def parse_binding(raw: dict) -> SourceBinding:
    allowed = {"id", "kind", "label", "space_id", "category"} if raw.get("kind") == "chat" else {
        "id", "kind", "label", "code_project_id", "environment", "paths"}
    if set(raw) - allowed:
        raise ValueError("來源包含不支援的欄位")
    ident = raw.get("id", "")
    label = raw.get("label", "")
    if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident):
        raise ValueError("來源識別值無效")
    if not isinstance(label, str) or not label.strip() or len(label) > 120:
        raise ValueError("來源名稱不可空白且最多 120 字")
    if raw.get("kind") == "chat":
        sid = raw.get("space_id", "")
        category = raw.get("category", "space")
        if not isinstance(sid, str) or not re.fullmatch(r"spaces/[^/\s]+", sid):
            raise ValueError("必須指定完整 Space ID")
        if category not in ("space", "pm"):
            raise ValueError("Chat 來源類別無效")
        return ChatBinding(ident, label.strip(), sid, category=category)
    if raw.get("kind") == "git":
        pid, env, paths = raw.get("code_project_id"), raw.get("environment"), raw.get("paths", [])
        if type(pid) is not int or pid < 1 or env not in ("production", "uat", "dev"):
            raise ValueError("Git 登錄或環境無效")
        if not isinstance(paths, list) or len(paths) > 20 or any(
            not isinstance(p, str) or not p or p.startswith(("/", "\\"))
            or ".." in p.replace("\\", "/").split("/") or "\\" in p or ":" in p
            for p in paths
        ):
            raise ValueError("Git 來源只能指定 repo 內相對路徑")
        return GitBinding(ident, label.strip(), pid, env, tuple(paths))
    raise ValueError("不支援的來源類型")


@dataclass(frozen=True)
class ApprovedSources:
    scope_json: str
    chat_ids: tuple[str, ...]
    git_refs: tuple[tuple[dict, str, str, list[str]], ...]

    @property
    def scope(self) -> dict:
        return json.loads(self.scope_json)


def make_scope(owner: int, mention: dict, profile: dict, selection: dict,
               projects: dict[int, dict], limit: int, terms: list[str],
               max_git: int) -> ApprovedSources:
    if selection["revision"] != profile["revision"]:
        raise ValueError("專案已更新，請重新選擇")
    if selection.get("target_space_id") != mention["space_id"] or (
        selection.get("target_thread_name") != mention.get("thread_name")
    ):
        raise ValueError("請確認本題確切的 Space 與討論串")
    choices = selection.get("sources", [])
    uses = {c["binding_id"]: c["use"] for c in choices}
    bindings = profile["sources"]
    if len(uses) != len(choices) or set(uses) != {b["id"] for b in bindings}:
        raise ValueError("本題來源必須與專案完整對應")
    if any(use not in ("metadata_only", "reply_context") for use in uses.values()):
        raise ValueError("來源用途無效")
    chats, git_refs, sources = [], [], []
    for raw in bindings:
        binding = parse_binding(raw)
        use = uses[binding.id]
        item = {**raw, "use": use}
        if isinstance(binding, ChatBinding):
            if use == "reply_context":
                chats.append(binding.space_id)
        else:
            project = projects.get(binding.code_project_id)
            if not project or not project["enabled"]:
                raise ValueError("Git 登錄不存在或已停用")
            branch = project["branches"].get(binding.environment)
            if not branch:
                raise ValueError("Git 登錄沒有此環境分支")
            item.update(registration_fingerprint=git_fingerprint(project), resolved_branch=branch)
            if use == "reply_context":
                git_refs.append((project, binding.environment, branch, list(binding.paths)))
        sources.append(item)
    if len(git_refs) > max_git:
        raise ValueError("本題 Git 來源超過上限")
    scope = dict(schema_version=1, owner_viewer_id=owner, mention_id=mention["id"],
                 message_name=mention["message_name"], target_space_id=mention["space_id"],
                 target_thread_name=mention.get("thread_name"), profile_id=profile["id"],
                 profile_name=profile["name"], revision=profile["revision"], sources=sources,
                 limit=limit, code_terms=terms)
    scope["digest"] = digest(scope)
    return ApprovedSources(json.dumps(scope, ensure_ascii=False), tuple(dict.fromkeys(chats)), tuple(git_refs))
