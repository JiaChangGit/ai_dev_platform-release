#!/usr/bin/env python3
"""管理本 release repository 的協作者、CODEOWNERS 與審查政策。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    "*",
    "/release-evidence/",
    "/release-notes/",
    "/scripts/",
    "/.github/",
    "/.gitlab/",
)
USERNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
PROJECT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
OWNER = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)"
    r"(?:/[A-Za-z0-9_.-]+)?"
)
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")


@dataclass(frozen=True)
class GitLabPreflightResult:
    project: dict[str, object]
    user_id: int
    branch: str | None
    protected_branch: dict[str, object] | None
    approval_rules: list[object]
    merge_request: dict[str, object] | None


class GitLabError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"GitLab API HTTP {status}: {detail}")
        self.status = status


def validate_username(value: str) -> str:
    if not USERNAME.fullmatch(value):
        raise ValueError(f"username 格式無效：{value}")
    return value


def validate_project(value: str) -> str:
    result = value.removesuffix(".git")
    if not PROJECT.fullmatch(result) or ".." in result.split("/"):
        raise ValueError(f"repository／project 路徑無效：{value}")
    return result


def selected_branch(
    override: str | None,
    metadata: dict[str, object],
    *,
    provider: str,
) -> str:
    if override:
        return override
    branch = metadata.get("default_branch")
    if not isinstance(branch, str) or not branch.strip():
        raise RuntimeError(f"{provider} repository 缺少有效 default_branch；請用 --branch 明確指定")
    return branch


def validate_owner(value: str) -> str:
    result = value.removeprefix("@")
    if not OWNER.fullmatch(result):
        raise ValueError(f"CODEOWNER 格式無效：{value}")
    return result


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def remote_targets(remote: str) -> tuple[str | None, str | None]:
    github = None
    gitlab = None
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if remote.startswith(prefix):
            github = validate_project(remote[len(prefix) :])
    for prefix in ("git@gitlab.com:", "https://gitlab.com/", "ssh://git@gitlab.com/"):
        if remote.startswith(prefix):
            gitlab = validate_project(remote[len(prefix) :])
    return github, gitlab


def read_codeowners() -> str:
    for path in (ROOT / ".github/CODEOWNERS", ROOT / ".gitlab/CODEOWNERS"):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def primary_owner(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for item in stripped.split()[1:]:
            if item.startswith("@"):
                return validate_owner(item)
    return None


def canonical_codeowners(text: str, username: str, owner: str) -> str:
    user_handle = f"@{validate_username(username)}"
    owner_handle = f"@{validate_owner(owner)}"
    lines: list[str] = []
    seen: set[str] = set()
    if not text.strip():
        lines.append("# GitHub／GitLab 共用 CODEOWNERS；由 scripts/manage_collaborators.py 維護。")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(line)
            continue
        parts = stripped.split()
        if len(parts) < 2:
            raise ValueError(f"CODEOWNERS 規則缺少 owner：{line}")
        seen.add(parts[0])
        existing = {item.lower() for item in parts[1:]}
        if owner_handle.lower() not in existing:
            parts.append(owner_handle)
        if user_handle.lower() not in existing:
            parts.append(user_handle)
        lines.append(" ".join(parts))
    if lines and lines[-1] and seen:
        lines.append("")
    for pattern in PATTERNS:
        if pattern not in seen:
            lines.append(f"{pattern} {owner_handle} {user_handle}")
    return "\n".join(lines).rstrip() + "\n"


def atomic_write(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.chmod(mode)
    temporary.replace(path)


def check_local_policy() -> list[str]:
    errors: list[str] = []
    github = ROOT / ".github/CODEOWNERS"
    gitlab = ROOT / ".gitlab/CODEOWNERS"
    if not github.is_file():
        errors.append("缺少 .github/CODEOWNERS")
    if not gitlab.is_file():
        errors.append("缺少 .gitlab/CODEOWNERS")
    if errors:
        return errors
    github_text = github.read_text(encoding="utf-8")
    gitlab_text = gitlab.read_text(encoding="utf-8")
    if github_text != gitlab_text:
        errors.append("GitHub／GitLab CODEOWNERS 不同步")
    seen: set[str] = set()
    for number, line in enumerate(github_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        seen.add(parts[0])
        owners = {item.lower() for item in parts[1:] if item.startswith("@")}
        if len(owners) < 2:
            errors.append(f"CODEOWNERS 第 {number} 行少於兩位不同 owner")
    for pattern in PATTERNS:
        if pattern not in seen:
            errors.append(f"CODEOWNERS 缺少規則：{pattern}")
    for relative in (
        ".github/workflows/repository-policy.yml",
        ".github/pull_request_template.md",
        ".gitlab/merge_request_templates/default.md",
        ".gitlab-ci.yml",
    ):
        if not (ROOT / relative).is_file():
            errors.append(f"缺少 repository 政策檔：{relative}")

    allowed_root_files = {
        ".gitignore",
        ".gitlab-ci.yml",
        "AGENTS.md",
        "CLAUDE.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "opencode.json",
    }
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts or path.is_dir():
            continue
        posix = relative.as_posix()
        allowed = (
            (len(relative.parts) == 1 and relative.name in allowed_root_files)
            or (relative.parts[0] == "release-evidence" and len(relative.parts) == 2
                and (relative.name == "README.md" or relative.suffix == ".json"))
            or (relative.parts[0] == "release-notes" and len(relative.parts) == 2
                and relative.suffix == ".md")
            or posix in {
                ".github/CODEOWNERS",
                ".github/dependabot.yml",
                ".github/pull_request_template.md",
                ".github/workflows/codeql.yml",
                ".github/workflows/repository-policy.yml",
                ".gitlab/CODEOWNERS",
                ".gitlab/merge_request_templates/default.md",
                "scripts/manage_collaborators.py",
            }
        )
        if not allowed:
            errors.append(f"release repository 不允許的檔案：{posix}")
    return errors


def gh(args: list[str], payload: object | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        input=None if payload is None else json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stdout.strip() or "GitHub API 呼叫失敗")
    return result.stdout.strip()


def gh_api(method: str, endpoint: str, payload: object | None = None) -> object:
    args = ["api", "--method", method, endpoint]
    if payload is not None:
        args.extend(["--input", "-"])
    output = gh(args, payload)
    return json.loads(output) if output else {}


def github_preflight(repository: str) -> dict[str, object]:
    if shutil.which("gh") is None:
        raise RuntimeError("找不到 gh；請先安裝 GitHub CLI 並執行 gh auth login")
    gh(["auth", "status"])
    project = gh_api("GET", f"repos/{repository}")
    if not isinstance(project, dict) or str(project.get("full_name", "")).lower() != repository.lower():
        raise RuntimeError(f"GitHub repository 不一致：{repository}")
    permissions = project.get("permissions", {})
    if not isinstance(permissions, dict) or permissions.get("admin") is not True:
        raise RuntimeError(f"目前 gh 身分沒有 repository 管理權限：{repository}")
    return project


def github_branch_protection_preflight(repository: str, branch: str) -> None:
    """先確認方案可使用 branch protection，避免遠端只套用一半。"""
    encoded_branch = urllib.parse.quote(branch, safe="")
    branch_endpoint = f"repos/{repository}/branches/{encoded_branch}"
    branch_result = subprocess.run(
        ["gh", "api", "--method", "GET", branch_endpoint],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if branch_result.returncode != 0:
        detail = branch_result.stdout.strip()
        raise RuntimeError(
            f"GitHub 分支不存在或無法讀取：{repository}/{branch}"
            + (f"；{detail}" if detail else "")
        )
    try:
        branch_data = json.loads(branch_result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"GitHub 分支回應格式無效：{repository}/{branch}"
        ) from error
    if not isinstance(branch_data, dict) or branch_data.get("name") != branch:
        raise RuntimeError(
            f"GitHub 分支回應格式或名稱不一致：{repository}/{branch}"
        )

    endpoint = f"{branch_endpoint}/protection"
    result = subprocess.run(
        ["gh", "api", "--method", "GET", endpoint],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode == 0:
        return
    if any(marker in output for marker in ('"status":"404"', '"status":404', "HTTP 404")):
        return
    if "Upgrade to GitHub Pro" in output and "make this repository public" in output:
        raise RuntimeError(
            "GitHub branch protection 不可用：私人 repository 必須使用 GitHub Pro、"
            "GitHub Team 或 GitHub Enterprise，或將 repository 改為公開。"
            "branch protection 是阻擋條件，因此腳本不會自動改用 --no-configure-policy"
        )
    raise RuntimeError(output or f"無法確認 GitHub branch protection：{repository}/{branch}")


def github_review_preflight(repository: str, username: str, pr_number: int) -> None:
    """確認 reviewer 與開啟中的 PR，避免設定完成後才發現審查目標無效。"""
    user = gh_api("GET", f"users/{urllib.parse.quote(username, safe='')}")
    if not isinstance(user, dict) or str(user.get("login", "")).lower() != username.lower():
        raise RuntimeError(f"GitHub reviewer 無法解析：{username}")
    pull = gh_api("GET", f"repos/{repository}/pulls/{pr_number}")
    if (
        not isinstance(pull, dict)
        or pull.get("number") != pr_number
        or pull.get("state") != "open"
    ):
        raise RuntimeError(f"GitHub PR 不存在、未開啟或回應格式無效：#{pr_number}")
    author = pull.get("user")
    if isinstance(author, dict) and str(author.get("login", "")).lower() == username.lower():
        raise RuntimeError(f"GitHub PR 作者不可成為自己的 reviewer：#{pr_number}")


def github_collaborator_permission(repository: str, username: str) -> str | None:
    """讀取已生效權限；尚未接受的邀請與非 collaborator 回傳 None。"""
    result = subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{repository}/collaborators/{username}/permission",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode != 0:
        if "HTTP 404" in output or "Not Found" in output:
            return None
        raise RuntimeError(output or "無法確認 GitHub collaborator 權限")
    try:
        data = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError("GitHub collaborator 權限回應格式無效") from error
    permission = data.get("permission") if isinstance(data, dict) else None
    if permission not in {"read", "triage", "write", "maintain", "admin"}:
        raise RuntimeError("GitHub collaborator 權限回應格式無效")
    return str(permission)


def configure_github_policy(
    repository: str,
    *,
    branch: str,
    approvals: int,
) -> None:
    gh_api(
        "PATCH",
        f"repos/{repository}",
        {
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
        },
    )
    gh_api(
        "PUT",
        f"repos/{repository}/branches/{urllib.parse.quote(branch, safe='')}/protection",
        {
            "required_status_checks": {
                "strict": True,
                "checks": [{"context": "repository-policy"}],
            },
            "enforce_admins": True,
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True,
                "required_approving_review_count": approvals,
                "require_last_push_approval": True,
            },
            "restrictions": None,
            "required_linear_history": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
            "block_creations": False,
            "required_conversation_resolution": True,
            "lock_branch": False,
            "allow_fork_syncing": True,
        },
    )


def github_add_collaborator(repository: str, username: str, permission: str) -> None:
    gh_api(
        "PUT",
        f"repos/{repository}/collaborators/{username}",
        {"permission": permission},
    )


def request_github_review(repository: str, username: str, pr_number: int) -> None:
    gh_api(
        "POST",
        f"repos/{repository}/pulls/{pr_number}/requested_reviewers",
        {"reviewers": [username]},
    )


def validate_gitlab_url(value: str, allow_http: bool) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
        raise ValueError("GitLab API URL 必須使用 HTTPS")
    if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("GitLab API URL 格式無效")
    return value.rstrip("/")


def gl(
    base: str,
    endpoint: str,
    token: str,
    *,
    method: str = "GET",
    payload: object | None = None,
) -> object:
    request = urllib.request.Request(
        f"{base}{endpoint}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        method=method,
        headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise GitLabError(error.code, detail or str(error.reason)) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"無法連線 GitLab API：{error.reason}") from error
    return json.loads(data) if data else {}


def gitlab_effective_access_level(project: dict[str, object]) -> int:
    permissions = project.get("permissions")
    if not isinstance(permissions, dict):
        return 0
    levels = []
    for key in ("project_access", "group_access"):
        access = permissions.get(key)
        if isinstance(access, dict) and isinstance(access.get("access_level"), int):
            levels.append(int(access["access_level"]))
    return max(levels, default=0)


def gitlab_user_id(api_url: str, token: str, username: str) -> int:
    query = urllib.parse.urlencode({"username": username, "active": "true"})
    users = gl(api_url, f"/users?{query}", token)
    exact = [
        item for item in users
        if isinstance(item, dict) and str(item.get("username", "")).lower() == username.lower()
    ] if isinstance(users, list) else []
    if len(exact) != 1 or not isinstance(exact[0].get("id"), int):
        raise RuntimeError(f"GitLab username 無法唯一解析：{username}")
    if exact[0].get("state") != "active":
        raise RuntimeError(f"GitLab username 不是 active 狀態：{username}")
    return int(exact[0]["id"])


def gitlab_token_preflight(api_url: str, token: str) -> None:
    info = gl(api_url, "/personal_access_tokens/self", token)
    if not isinstance(info, dict):
        raise RuntimeError("GitLab Token 自我查詢回應格式無效")
    if info.get("active") is not True or info.get("revoked") is not False:
        raise RuntimeError("GitLab Token 已停用、撤銷或過期")
    scopes = info.get("scopes")
    if not isinstance(scopes, list) or "api" not in scopes:
        raise RuntimeError(
            "GitLab Token 缺少 api scope；read_api 與 write_repository 不足以管理 member 與 project policy"
        )


def gitlab_direct_member_exists(
    api_url: str,
    project_endpoint: str,
    *,
    token: str,
    user_id: int,
) -> bool:
    try:
        member = gl(api_url, f"{project_endpoint}/members/{user_id}", token)
    except GitLabError as error:
        if error.status == 404:
            return False
        raise
    if not isinstance(member, dict) or member.get("state") != "active":
        raise RuntimeError(f"GitLab direct member 回應格式或狀態無效：{user_id}")
    return True


def gitlab_membership_lock_preflight(
    api_url: str,
    project: dict[str, object],
    *,
    token: str,
) -> None:
    namespace = project.get("namespace")
    if not isinstance(namespace, dict):
        raise RuntimeError("GitLab project 缺少 namespace，無法確認 membership lock")
    kind = namespace.get("kind")
    if kind == "user":
        return
    if kind != "group":
        raise RuntimeError(f"GitLab project namespace kind 無法辨識：{kind}")
    group_id = namespace.get("id")
    if not isinstance(group_id, int):
        raise RuntimeError("GitLab project 缺少 immediate group id，無法確認 membership lock")
    group = gl(api_url, f"/groups/{group_id}", token)
    if not isinstance(group, dict):
        raise RuntimeError(f"GitLab group 回應格式無效：{group_id}")
    membership_lock = group.get("membership_lock")
    if not isinstance(membership_lock, bool):
        raise RuntimeError(
            f"無法確認 GitLab group membership lock：{group_id}；欄位缺少或型別無效"
        )
    if membership_lock:
        raise RuntimeError(
            f"GitLab group membership lock 已啟用，無法新增 project member：{group_id}"
        )


def gitlab_protected_branch_preflight(
    api_url: str,
    project_endpoint: str,
    *,
    token: str,
    branch: str,
) -> dict[str, object] | None:
    endpoint = f"{project_endpoint}/protected_branches/{urllib.parse.quote(branch, safe='')}"
    try:
        current = gl(api_url, endpoint, token)
    except GitLabError as error:
        if error.status == 404:
            return None
        raise
    if not isinstance(current, dict) or current.get("name") != branch:
        raise RuntimeError(f"GitLab protected branch 回應格式或名稱不一致：{branch}")
    for field in ("push_access_levels", "merge_access_levels", "unprotect_access_levels"):
        records = current.get(field)
        if not isinstance(records, list):
            raise RuntimeError(f"GitLab protected branch 缺少 {field}：{branch}")
        for record in records:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("id"), int)
                or not isinstance(record.get("access_level"), int)
            ):
                raise RuntimeError(f"GitLab protected branch 的 {field} 格式無效：{branch}")
    for field in ("allow_force_push", "code_owner_approval_required"):
        if not isinstance(current.get(field), bool):
            raise RuntimeError(f"GitLab protected branch 缺少有效 {field}：{branch}")
    return current


def gitlab_review_preflight(
    api_url: str,
    project_endpoint: str,
    *,
    token: str,
    user_id: int,
    merge_request_iid: int,
) -> dict[str, object]:
    merge_request = gl(
        api_url,
        f"{project_endpoint}/merge_requests/{merge_request_iid}",
        token,
    )
    if (
        not isinstance(merge_request, dict)
        or merge_request.get("iid") != merge_request_iid
        or merge_request.get("state") != "opened"
        or not isinstance(merge_request.get("reviewers"), list)
    ):
        raise RuntimeError(
            f"GitLab MR 不存在、未開啟或回應格式無效：!{merge_request_iid}"
        )
    author = merge_request.get("author")
    if isinstance(author, dict) and author.get("id") == user_id:
        raise RuntimeError(f"GitLab MR 作者不可成為自己的 reviewer：!{merge_request_iid}")
    for reviewer in merge_request["reviewers"]:
        if not isinstance(reviewer, dict) or not isinstance(reviewer.get("id"), int):
            raise RuntimeError(f"GitLab MR reviewer 回應格式無效：!{merge_request_iid}")
    return merge_request


def gitlab_preflight(
    api_url: str,
    project_name: str,
    *,
    token: str,
    username: str,
    branch_override: str | None,
    configure_policy: bool,
    merge_request_iid: int | None = None,
) -> GitLabPreflightResult:
    """唯讀確認 GitLab 權限、分支與付費政策 API，再允許任何寫入。"""
    gitlab_token_preflight(api_url, token)
    project_endpoint = f"/projects/{urllib.parse.quote(project_name, safe='')}"
    project = gl(api_url, project_endpoint, token)
    if not isinstance(project, dict):
        raise RuntimeError(f"GitLab project 回應格式無效：{project_name}")
    if gitlab_effective_access_level(project) < 40:
        raise RuntimeError(
            f"目前 GitLab Token 沒有 Maintainer 或 Owner 權限：{project_name}"
        )

    user_id = gitlab_user_id(api_url, token, username)
    direct_member = gitlab_direct_member_exists(
        api_url,
        project_endpoint,
        token=token,
        user_id=user_id,
    )
    if not direct_member:
        gitlab_membership_lock_preflight(api_url, project, token=token)

    branch = selected_branch(
        branch_override,
        project,
        provider="GitLab",
    ) if configure_policy else None
    protected_branch: dict[str, object] | None = None
    rules: list[object] = []
    if configure_policy:
        if branch is None:
            raise RuntimeError("GitLab 分支預檢狀態遺失")
        encoded_branch = urllib.parse.quote(branch, safe="")
        try:
            branch_data = gl(
                api_url,
                f"{project_endpoint}/repository/branches/{encoded_branch}",
                token,
            )
        except GitLabError as error:
            raise RuntimeError(
                f"GitLab 分支不存在或無法讀取：{project_name}/{branch}；{error}"
            ) from error
        if not isinstance(branch_data, dict) or branch_data.get("name") != branch:
            raise RuntimeError(
                f"GitLab 分支回應格式或名稱不一致：{project_name}/{branch}"
            )
        protected_branch = gitlab_protected_branch_preflight(
            api_url,
            project_endpoint,
            token=token,
            branch=branch,
        )
        try:
            approvals = gl(api_url, f"{project_endpoint}/approvals", token)
            approval_rules = gl(api_url, f"{project_endpoint}/approval_rules", token)
        except GitLabError as error:
            if error.status in (403, 404):
                raise RuntimeError(
                    "GitLab 必要核准政策不可用：Code Owner 與 required approval rule "
                    "需要 GitLab Premium／Ultimate，以及 Maintainer 或 Owner 權限"
                ) from error
            raise
        if not isinstance(approvals, dict) or not isinstance(approval_rules, list):
            raise RuntimeError(f"GitLab 核准政策回應格式無效：{project_name}")
        rules = approval_rules

    merge_request = None
    if merge_request_iid is not None:
        merge_request = gitlab_review_preflight(
            api_url,
            project_endpoint,
            token=token,
            user_id=user_id,
            merge_request_iid=merge_request_iid,
        )
    return GitLabPreflightResult(
        project=project,
        user_id=user_id,
        branch=branch,
        protected_branch=protected_branch,
        approval_rules=rules,
        merge_request=merge_request,
    )


def gitlab_access_updates(records: object, access_level: int) -> list[dict[str, object]]:
    """保留一條一般層級規則，移除使用者、群組與 deploy key 例外。"""
    if not isinstance(records, list):
        raise RuntimeError("GitLab protected branch access level 預檢狀態遺失")
    updates: list[dict[str, object]] = []
    generic_seen = False
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), int):
            raise RuntimeError("GitLab protected branch access level 預檢狀態無效")
        special = any(
            record.get(field) is not None
            for field in ("user_id", "group_id", "deploy_key_id")
        )
        if special or generic_seen:
            updates.append({"id": record["id"], "_destroy": True})
        else:
            updates.append({"id": record["id"], "access_level": access_level})
            generic_seen = True
    if not generic_seen:
        updates.append({"access_level": access_level})
    return updates


def configure_gitlab_policy(
    project_name: str,
    *,
    api_url: str,
    token: str,
    approvals: int,
    preflight: GitLabPreflightResult,
) -> None:
    project_endpoint = f"/projects/{urllib.parse.quote(project_name, safe='')}"
    branch = preflight.branch
    if branch is None:
        raise RuntimeError("GitLab 分支預檢狀態遺失；未設定 protected branch")
    gl(
        api_url,
        project_endpoint,
        token,
        method="PUT",
        payload={
            "only_allow_merge_if_pipeline_succeeds": True,
            "only_allow_merge_if_all_discussions_are_resolved": True,
            "remove_source_branch_after_merge": True,
        },
    )
    protected = f"{project_endpoint}/protected_branches/{urllib.parse.quote(branch, safe='')}"
    current = preflight.protected_branch
    if current is None:
        gl(
            api_url,
            f"{project_endpoint}/protected_branches",
            token,
            method="POST",
            payload={
                "name": branch,
                "push_access_level": 0,
                "merge_access_level": 30,
                "unprotect_access_level": 40,
                "allow_force_push": False,
                "code_owner_approval_required": True,
            },
        )
    else:
        gl(
            api_url,
            protected,
            token,
            method="PATCH",
            payload={
                "allow_force_push": False,
                "code_owner_approval_required": True,
                "allowed_to_push": gitlab_access_updates(current["push_access_levels"], 0),
                "allowed_to_merge": gitlab_access_updates(current["merge_access_levels"], 30),
                "allowed_to_unprotect": gitlab_access_updates(current["unprotect_access_levels"], 40),
            },
        )

    gl(
        api_url,
        f"{project_endpoint}/approvals",
        token,
        method="POST",
        payload={
            "reset_approvals_on_push": True,
            "disable_overriding_approvers_per_merge_request": True,
            "merge_requests_author_approval": False,
            "merge_requests_disable_committers_approval": True,
        },
    )
    named = [
        rule
        for rule in preflight.approval_rules
        if isinstance(rule, dict) and rule.get("name") == "Repository policy"
    ]
    rule = {
        "name": "Repository policy",
        "approvals_required": approvals,
        "applies_to_all_protected_branches": True,
    }
    if named and isinstance(named[0].get("id"), int):
        gl(api_url, f"{project_endpoint}/approval_rules/{named[0]['id']}", token, method="PUT", payload=rule)
    else:
        gl(api_url, f"{project_endpoint}/approval_rules", token, method="POST", payload=rule)


def gitlab_add_member(
    api_url: str,
    project_endpoint: str,
    *,
    token: str,
    user_id: int,
    access_level: int,
) -> None:
    try:
        gl(
            api_url,
            f"{project_endpoint}/members",
            token,
            method="POST",
            payload={"user_id": user_id, "access_level": access_level},
        )
    except GitLabError as error:
        if error.status != 409:
            raise
        gl(
            api_url,
            f"{project_endpoint}/members/{user_id}",
            token,
            method="PUT",
            payload={"access_level": access_level},
        )


def request_gitlab_review(
    api_url: str,
    project_endpoint: str,
    *,
    token: str,
    user_id: int,
    merge_request_iid: int,
    current: dict[str, object],
) -> None:
    reviewer_ids = {item["id"] for item in current["reviewers"]}
    reviewer_ids.add(user_id)
    gl(
        api_url,
        f"{project_endpoint}/merge_requests/{merge_request_iid}",
        token,
        method="PUT",
        payload={"reviewer_ids": sorted(reviewer_ids)},
    )


def add(args: argparse.Namespace) -> int:
    username = validate_username(args.username)
    preflight_only = bool(getattr(args, "preflight_only", False))
    try:
        remote = git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        remote = ""
    auto_github, auto_gitlab = remote_targets(remote)
    github = validate_project(args.github_repository) if args.github_repository else auto_github
    gitlab = validate_project(args.gitlab_project) if args.gitlab_project else auto_gitlab
    if args.skip_github:
        github = None
    if args.skip_gitlab:
        gitlab = None
    if args.local_only and (
        args.request_review is not None or args.request_merge_request_review is not None
    ):
        raise ValueError("--local-only 不可搭配遠端 reviewer 參數")
    if preflight_only and (args.apply or args.local_only):
        raise ValueError("--preflight-only 不可搭配 --apply 或 --local-only")
    if args.request_review is not None and not github:
        raise ValueError("--request-review 必須搭配可用的 GitHub repository")
    if args.request_merge_request_review is not None and not gitlab:
        raise ValueError("--request-merge-request-review 必須搭配可用的 GitLab project")
    if (args.apply or preflight_only) and not args.local_only and not github and not gitlab:
        raise RuntimeError("沒有可處理的 GitHub／GitLab 目標；只改本機請加上 --local-only")
    if (args.apply or preflight_only) and not args.local_only and not args.configure_policy:
        raise ValueError("平台將分支保護列為阻擋條件，不接受 --no-configure-policy")
    current = read_codeowners()
    owner = validate_owner(args.owner) if args.owner else primary_owner(current)
    if not owner:
        if github:
            owner = validate_owner(github.split("/", 1)[0])
        else:
            raise ValueError("新的 CODEOWNERS 必須用 --owner 指定既有負責人或 team")
    content = canonical_codeowners(current, username, owner)

    print(f"[PLAN] CODEOWNERS: @{owner} + @{username}")
    if github:
        print(f"[PLAN] GitHub: {github}")
    if gitlab:
        print(f"[PLAN] GitLab: {gitlab}; token env={args.gitlab_token_env}")
    if not args.apply and not preflight_only:
        print("[DRY-RUN] 未修改檔案或遠端設定；確認後加上 --apply")
        return 0

    github_project: dict[str, object] | None = None
    github_branch: str | None = None
    github_current_permission: str | None = None
    if github and not args.local_only:
        github_project = github_preflight(github)
        github_current_permission = github_collaborator_permission(github, username)
        if args.configure_policy:
            github_branch = selected_branch(
                args.branch,
                github_project,
                provider="GitHub",
            )
            github_branch_protection_preflight(github, github_branch)
        if args.request_review is not None:
            github_review_preflight(github, username, args.request_review)

    gitlab_api_url: str | None = None
    gitlab_token: str | None = None
    gitlab_preflight_result: GitLabPreflightResult | None = None
    if gitlab and not args.local_only:
        if not ENV_NAME.fullmatch(args.gitlab_token_env):
            raise ValueError("GitLab token 環境變數名稱格式無效")
        gitlab_token = os.environ.get(args.gitlab_token_env, "")
        if not gitlab_token:
            raise RuntimeError(f"缺少 GitLab token 環境變數：{args.gitlab_token_env}")
        gitlab_api_url = validate_gitlab_url(
            args.gitlab_api_url,
            args.allow_insecure_gitlab_http,
        )
        gitlab_preflight_result = gitlab_preflight(
            gitlab_api_url,
            gitlab,
            token=gitlab_token,
            username=username,
            branch_override=args.branch,
            configure_policy=args.configure_policy,
            merge_request_iid=args.request_merge_request_review,
        )

    if preflight_only:
        print("[OK] GitHub／GitLab 遠端唯讀預檢通過；未修改檔案或遠端設定")
        return 0

    if args.local_only:
        atomic_write(ROOT / ".github/CODEOWNERS", content)
        atomic_write(ROOT / ".gitlab/CODEOWNERS", content)
        print("[OK] GitHub／GitLab CODEOWNERS 已同步")
        return 0

    # 先完成所有遠端保護政策，再授予可寫權限。後段失敗時，新成員不會
    # 留在未受保護的分支上。
    if github:
        if github_branch is None:
            raise RuntimeError("GitHub 分支預檢狀態遺失；未設定 branch protection")
        configure_github_policy(
            github,
            branch=github_branch,
            approvals=args.approvals,
        )
        print(f"[OK] GitHub PR／branch protection 已設定：{github_branch}")

    if gitlab:
        if (
            gitlab_token is None
            or gitlab_api_url is None
            or gitlab_preflight_result is None
        ):
            raise RuntimeError("GitLab 預檢狀態遺失；未執行任何 GitLab 寫入")
        configure_gitlab_policy(
            gitlab,
            api_url=gitlab_api_url,
            token=gitlab_token,
            approvals=args.approvals,
            preflight=gitlab_preflight_result,
        )
        print("[OK] GitLab MR policy 與 protected branch 已設定")

    if github and github_current_permission is None:
        github_add_collaborator(github, username, "pull")
        print(
            f"[WAIT] GitHub 已送出唯讀邀請：{username}；接受後以相同參數重跑",
            file=sys.stderr,
        )
        print("[NEXT] 對方接受邀請後，以相同參數重跑一次 --apply", file=sys.stderr)
        return 2

    if github:
        github_add_collaborator(github, username, args.github_permission)
        print(f"[OK] GitHub collaborator 權限已設定：{username} ({args.github_permission})")

    if gitlab:
        if gitlab_preflight_result is None or gitlab_api_url is None or gitlab_token is None:
            raise RuntimeError("GitLab 預檢狀態遺失；未授予 member 權限")
        project_endpoint = f"/projects/{urllib.parse.quote(gitlab, safe='')}"
        gitlab_add_member(
            gitlab_api_url,
            project_endpoint,
            token=gitlab_token,
            user_id=gitlab_preflight_result.user_id,
            access_level=args.gitlab_access_level,
        )
        print(f"[OK] GitLab member 已設定：{username}")

    atomic_write(ROOT / ".github/CODEOWNERS", content)
    atomic_write(ROOT / ".gitlab/CODEOWNERS", content)
    print("[OK] GitHub／GitLab CODEOWNERS 已同步")

    if github and args.request_review is not None:
        request_github_review(github, username, args.request_review)
        print(f"[OK] GitHub PR #{args.request_review} 已加入 reviewer")

    if gitlab and args.request_merge_request_review is not None:
        if (
            gitlab_preflight_result is None
            or gitlab_preflight_result.merge_request is None
            or gitlab_api_url is None
            or gitlab_token is None
        ):
            raise RuntimeError("GitLab MR 預檢狀態遺失；未送出 reviewer 變更")
        request_gitlab_review(
            gitlab_api_url,
            f"/projects/{urllib.parse.quote(gitlab, safe='')}",
            token=gitlab_token,
            user_id=gitlab_preflight_result.user_id,
            merge_request_iid=args.request_merge_request_review,
            current=gitlab_preflight_result.merge_request,
        )
        print(f"[OK] GitLab MR !{args.request_merge_request_review} 已加入 reviewer")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="驗證 CODEOWNERS 與 CI 政策檔")
    command = subparsers.add_parser("add", help="新增 collaborator／member 並設定審查政策")
    command.add_argument("username")
    command.add_argument("--apply", action="store_true")
    command.add_argument(
        "--preflight-only",
        action="store_true",
        help="執行 GitHub／GitLab 遠端唯讀預檢，不寫入設定",
    )
    command.add_argument("--local-only", action="store_true")
    command.add_argument("--owner")
    command.add_argument("--github-repository")
    command.add_argument("--skip-github", action="store_true")
    command.add_argument(
        "--github-permission",
        choices=("push", "maintain", "admin"),
        default="push",
        help="GitHub 權限；CODEOWNERS 至少需要 write（push）",
    )
    command.add_argument("--request-review", type=int, metavar="PR_NUMBER")
    command.add_argument("--gitlab-project")
    command.add_argument("--skip-gitlab", action="store_true")
    command.add_argument("--gitlab-api-url", default="https://gitlab.com/api/v4")
    command.add_argument("--gitlab-token-env", default="GITLAB_TOKEN")
    command.add_argument("--gitlab-access-level", choices=(30, 40), type=int, default=30)
    command.add_argument("--request-merge-request-review", type=int, metavar="MR_IID")
    command.add_argument("--allow-insecure-gitlab-http", action="store_true")
    command.add_argument("--branch")
    command.add_argument("--approvals", choices=range(1, 7), type=int, default=1)
    command.add_argument("--configure-policy", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    try:
        if args.command == "check":
            errors = check_local_policy()
            if errors:
                for error in errors:
                    print(f"[FAIL] collaborator policy: {error}", file=sys.stderr)
                return 1
            print("[OK] collaborator policy: release")
            return 0
        return add(args)
    except (GitLabError, OSError, ValueError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"[FAIL] collaborator management: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
