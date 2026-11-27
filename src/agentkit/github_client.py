"""GitHub REST client (stdlib urllib). Scoped to TARGET_REPO only.

The token in .env is repo-scoped by instruction; this module additionally
refuses to act on any repo other than TARGET_REPO (defense in depth).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class GitHubClient:
    API = "https://api.github.com"

    def __init__(self, cfg: dict):
        self.token = cfg.get("GITHUB_TOKEN", "")
        self.target = cfg.get("TARGET_REPO", "")
        self.session = urllib.request  # stdlib

    def _req(self, method: str, path: str, body: object | None = None):
        req = urllib.request.Request(
            f"{self.API}{path}", method=method,
            data=json.dumps(body).encode() if body else None,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "agentkit-triage/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            txt = resp.read().decode()
            return json.loads(txt) if txt else {}

    def _assert_target(self, repo: str) -> None:
        if repo != self.target:
            raise PermissionError(
                f"refusing: repo {repo!r} is not the configured TARGET_REPO")

    def get_pull(self, repo: str, number: int) -> dict:
        self._assert_target(repo)
        return self._req("GET", f"/repos/{repo}/pulls/{number}")

    def post_comment(self, repo: str, number: int, body: str) -> dict:
        self._assert_target(repo)
        return self._req("POST", f"/repos/{repo}/issues/{number}/comments",
                         {"body": body})

    def add_labels(self, repo: str, number: int, labels: list[str]) -> dict:
        self._assert_target(repo)
        return self._req("POST", f"/repos/{repo}/issues/{number}/labels", labels)
