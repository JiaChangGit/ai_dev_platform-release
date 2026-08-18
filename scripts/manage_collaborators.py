#!/usr/bin/env python3
"""管理本 release repository 的協作者、CODEOWNERS 與審查政策。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
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
OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[A-Za-z0-9_.-]+)?")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
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
        "README.md",
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
                ".github/pull_request_template.md",
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


def github_active(repository: str, username: str) -> bool:
    result = subprocess.run(
        ["gh", "api", "--silent", f"repos/{repository}/collaborators/{username}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode == 0:
        return True
    if "HTTP 404" in result.stdout or "Not Found" in result.stdout:
        return False
    raise RuntimeError(result.stdout.strip() or "無法確認 GitHub collaborator 狀態")


def apply_github(
    repository: str,
    username: str,
    *,
    permission: str,
    approvals: int,
    branch_override: str | None,
    request_review: int | None,
    configure_policy: bool,
) -> bool:
    if shutil.which("gh") is None:
        raise RuntimeError("找不到 gh；請先安裝 GitHub CLI 並執行 gh auth login")
    gh(["auth", "status"])
    project = gh_api("GET", f"repos/{repository}")
    permissions = project.get("permissions", {}) if isinstance(project, dict) else {}
    if not isinstance(permissions, dict) or permissions.get("admin") is not True:
        raise RuntimeError(f"目前 gh 身分沒有 repository 管理權限：{repository}")
    gh_api(
        "PUT",
        f"repos/{repository}/collaborators/{username}",
        {"permission": permission},
    )
    if not github_active(repository, username):
        return False
    if configure_policy:
        branch = branch_override or str(project.get("default_branch") or "main")
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
    if request_review is not None:
        gh_api(
            "POST",
            f"repos/{repository}/pulls/{request_review}/requested_reviewers",
            {"reviewers": [username]},
        )
    return True


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


def apply_gitlab(
    project_name: str,
    username: str,
    *,
    api_url: str,
    token: str,
    access_level: int,
    approvals: int,
    branch_override: str | None,
    request_review: int | None,
    configure_policy: bool,
) -> None:
    project_endpoint = f"/projects/{urllib.parse.quote(project_name, safe='')}"
    project = gl(api_url, project_endpoint, token)
    users = gl(api_url, f"/users?{urllib.parse.urlencode({'username': username})}", token)
    exact = [
        item for item in users
        if isinstance(item, dict) and str(item.get("username", "")).lower() == username.lower()
    ] if isinstance(users, list) else []
    if len(exact) != 1 or not isinstance(exact[0].get("id"), int):
        raise RuntimeError(f"GitLab username 無法唯一解析：{username}")
    user_id = int(exact[0]["id"])
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

    if configure_policy:
        if not isinstance(project, dict):
            raise RuntimeError("GitLab project 回應格式無效")
        branch = branch_override or str(project.get("default_branch") or "main")
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
        try:
            current = gl(api_url, protected, token)
        except GitLabError as error:
            if error.status != 404:
                raise
            current = None
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
            push_updates = []
            if isinstance(current, dict):
                for record in current.get("push_access_levels", []):
                    if isinstance(record, dict) and isinstance(record.get("id"), int):
                        push_updates.append(
                            {"id": record["id"], "_destroy": True}
                            if record.get("deploy_key_id") is not None
                            else {"id": record["id"], "access_level": 0}
                        )
            payload: dict[str, object] = {
                "allow_force_push": False,
                "code_owner_approval_required": True,
            }
            if push_updates:
                payload["allowed_to_push"] = push_updates
            gl(api_url, protected, token, method="PATCH", payload=payload)

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
        rules = gl(api_url, f"{project_endpoint}/approval_rules", token)
        named = [r for r in rules if isinstance(r, dict) and r.get("name") == "Repository policy"] \
            if isinstance(rules, list) else []
        rule = {
            "name": "Repository policy",
            "approvals_required": approvals,
            "applies_to_all_protected_branches": True,
        }
        if named and isinstance(named[0].get("id"), int):
            gl(api_url, f"{project_endpoint}/approval_rules/{named[0]['id']}", token, method="PUT", payload=rule)
        else:
            gl(api_url, f"{project_endpoint}/approval_rules", token, method="POST", payload=rule)

    if request_review is not None:
        endpoint = f"{project_endpoint}/merge_requests/{request_review}"
        merge_request = gl(api_url, endpoint, token)
        reviewers = merge_request.get("reviewers", []) if isinstance(merge_request, dict) else []
        reviewer_ids = {
            item["id"] for item in reviewers
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        reviewer_ids.add(user_id)
        gl(api_url, endpoint, token, method="PUT", payload={"reviewer_ids": sorted(reviewer_ids)})


def add(args: argparse.Namespace) -> int:
    username = validate_username(args.username)
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
    if args.apply and not args.local_only and not github and not gitlab:
        raise RuntimeError("沒有可處理的 GitHub／GitLab 目標；只改本機請加上 --local-only")
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
    if not args.apply:
        print("[DRY-RUN] 未修改檔案或遠端設定；確認後加上 --apply")
        return 0
    atomic_write(ROOT / ".github/CODEOWNERS", content)
    atomic_write(ROOT / ".gitlab/CODEOWNERS", content)
    print("[OK] GitHub／GitLab CODEOWNERS 已同步")
    if args.local_only:
        return 0
    pending = False
    if github:
        active = apply_github(
            github,
            username,
            permission=args.github_permission,
            approvals=args.approvals,
            branch_override=args.branch,
            request_review=args.request_review,
            configure_policy=args.configure_policy,
        )
        if active:
            print("[OK] GitHub collaborator 已生效")
            if args.configure_policy:
                print("[OK] GitHub PR 與 branch protection 已處理")
            if args.request_review is not None:
                print(f"[OK] GitHub PR #{args.request_review} 已加入 reviewer")
        else:
            pending = True
            print("[WAIT] GitHub 邀請尚未接受，未啟用 branch protection 或指定 reviewer", file=sys.stderr)

    if gitlab:
        if not ENV_NAME.fullmatch(args.gitlab_token_env):
            raise ValueError("GitLab token 環境變數名稱格式無效")
        token = os.environ.get(args.gitlab_token_env, "")
        if not token:
            raise RuntimeError(f"缺少 GitLab token 環境變數：{args.gitlab_token_env}")
        api_url = validate_gitlab_url(args.gitlab_api_url, args.allow_insecure_gitlab_http)
        apply_gitlab(
            gitlab,
            username,
            api_url=api_url,
            token=token,
            access_level=args.gitlab_access_level,
            approvals=args.approvals,
            branch_override=args.branch,
            request_review=args.request_merge_request_review,
            configure_policy=args.configure_policy,
        )
        print("[OK] GitLab member、MR 與 protected branch 已處理")

    if pending:
        print("[NEXT] 對方接受邀請後，以相同參數重跑一次 --apply", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="驗證 CODEOWNERS 與 CI 政策檔")
    command = subparsers.add_parser("add", help="新增 collaborator／member 並設定審查政策")
    command.add_argument("username")
    command.add_argument("--apply", action="store_true")
    command.add_argument("--local-only", action="store_true")
    command.add_argument("--owner")
    command.add_argument("--github-repository")
    command.add_argument("--skip-github", action="store_true")
    command.add_argument("--github-permission", choices=("pull", "triage", "push", "maintain", "admin"), default="push")
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
