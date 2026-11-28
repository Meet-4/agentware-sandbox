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

    def list_pr_files(self, repo: str, number: int):
        """First page of changed files in a PR (path, contents via patch)."""
        self._assert_target(repo)
        files = self._req(  # type: ignore[assignment]
            "GET", f"/repos/{repo}/pulls/{number}/files?per_page=100")
        return files

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """Full content of one file at a ref (base64 API decode)."""
        self._assert_target(repo)
        import base64
        import urllib.parse
        q = urllib.parse.quote(path, safe="/")
        data = self._req("GET", f"/repos/{repo}/contents/{q}?ref={ref}")
        return base64.b64decode(data["content"]).decode(errors="replace")

    # ---- CI-failure path -------------------------------------------------

    def get_check_run(self, repo: str, check_run_id: int) -> dict:
        """One check run by id (name, conclusion, details_url, ...)."""
        self._assert_target(repo)
        return self._req("GET", f"/repos/{repo}/check-runs/{check_run_id}")

    def get_job_log(self, repo: str, details_url: str,
                    max_chars: int = 8000) -> str:
        """Tail of a failed job's log.

        details_url comes from the GitHub API (not the raw webhook body) but
        is still treated as untrusted: it must point at
        github.com/<TARGET_REPO>/actions/runs/N/jobs/M or it is refused.
        The log itself is hostile text — the caller must keep it as data.
        """
        import re
        import urllib.parse
        import urllib.request
        self._assert_target(repo)
        m = re.fullmatch(
            rf"https://github\.com/{re.escape(repo)}/actions/runs/\d+/jobs?/(\d+)",
            details_url or "")
        if not m:
            raise PermissionError(
                f"refusing details_url {details_url!r} "
                "(not an actions job URL for TARGET_REPO)")
        # the endpoint 302s to a signed blob URL; never forward the token there
        class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                r = super().redirect_request(req, fp, code, msg,
                                             headers, newurl)
                if r is not None:
                    del r.headers["Authorization"]
                    r.unverifiable = True
                return r
        opener = urllib.request.build_opener(_NoAuthRedirect)
        req = urllib.request.Request(
            f"{self.API}/repos/{repo}/actions/jobs/{m.group(1)}/logs",
            headers={"Authorization": f"Bearer {self.token}",
                     "User-Agent": "agentkit-triage/0.1"})
        with opener.open(req, timeout=60) as resp:
            log = resp.read().decode(errors="replace")
        return log[-max_chars:]
