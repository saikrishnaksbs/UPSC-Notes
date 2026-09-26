"""Optional Git integration: commit exactly the files the app wrote, optionally push + open a PR.

Commits use `git commit --only <paths>`, so anything else the user has staged is left alone.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class GitError(RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc


def status(root: Path) -> dict:
    if shutil.which("git") is None:
        return {"is_repo": False, "error": "git is not installed"}
    inside = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0:
        return {"is_repo": False}
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    porcelain = _git(root, "status", "--porcelain", check=False).stdout.splitlines()
    remote = _git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    return {
        "is_repo": True,
        "branch": branch,
        "changed_files": len(porcelain),
        "remote": remote or None,
        "gh_available": shutil.which("gh") is not None,
    }


def safe_branch_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._/-]+", "-", name).strip("-/.")
    name = re.sub(r"\.{2,}|/{2,}", "-", name)
    return name or "notes/update"


def commit(root: Path, paths: list[str], message: str, branch: Optional[str] = None) -> dict:
    if not paths:
        raise GitError("Nothing to commit")
    st = status(root)
    if not st.get("is_repo"):
        raise GitError("The notes folder is not a Git repository")
    created = False
    if branch:
        branch = safe_branch_name(branch)
        if branch != st["branch"]:
            exists = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0
            if exists:
                _git(root, "switch", branch)
            else:
                _git(root, "switch", "-c", branch)
                created = True
    _git(root, "add", "--", *paths)
    _git(root, "commit", "--only", "-m", message, "--", *paths)
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    return {"branch": branch or st["branch"], "commit": sha, "created_branch": created}


def push_and_open_pr(root: Path, branch: str, title: str, body: str, remote: str = "origin", base: Optional[str] = None) -> dict:
    _git(root, "push", "-u", remote, branch)
    if shutil.which("gh") is None:
        return {"pushed": True, "pr_url": None, "note": "GitHub CLI (gh) not installed; open the PR on GitHub."}
    args = ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body]
    if base:
        args += ["--base", base]
    proc = subprocess.run(args, cwd=str(root), capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise GitError(f"gh pr create failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    url = next((ln for ln in proc.stdout.splitlines() if ln.startswith("http")), proc.stdout.strip())
    return {"pushed": True, "pr_url": url}
