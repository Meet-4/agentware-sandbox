"""Unattended trigger layer: GitHub webhook server + polling mode.

Two ways the agent wakes with no human:
- `python -m agentkit.trigger serve` — stdlib HTTP server. POST /webhook
  accepts a GitHub pull_request-opened event (HMAC-SHA256 verified when
  WEBHOOK_SECRET is set), triages the PR in a worker thread, posts the
  review. GET /stop is the KILL SWITCH: it sets a flag the pipeline checks
  between every step, cancelling the run mid-flight (audit trail still
  written). GET /health and GET /runs are read-only introspection.
- `python -m agentkit.trigger poll --interval 300` — judges without a
  public endpoint: lists open PRs on TARGET_REPO every N seconds and
  triages any PR it has not yet reviewed (state file on disk).

Webhook payload bodies are UNTRUSTED: only repo/full_name, number and
action are read, and each value is validated against TARGET_REPO / ints
before use — everything else is ignored, never passed to the LLM prompt
as instructions.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .github_client import GitHubClient
from .pipeline import load_env, run
from .triage import ReviewCase

# a PR number is the only thing we accept from a payload as a selector
_INT_RE = re.compile(r"^\d+$")


class RunRegistry:
    """Tracks live runs so /stop can kill the one in flight."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.lock = threading.Lock()
        self.active: dict[str, Path] = {}   # run_id -> kill-flag path
        self.history: list[dict] = []

    def kill_all(self) -> int:
        with self.lock:
            self.active.clear()
        # global flag: every live run's KillSwitch checks traces/KILL
        flag = self.out_dir / "traces" / "KILL"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("STOP requested via /stop\n", encoding="utf-8")
        # auto-clear so later runs are not permanently blocked; in-flight
        # runs check the flag between every pipeline step (well inside 120s)
        def _clear():
            time.sleep(120)
            try:
                flag.unlink()
            except FileNotFoundError:
                pass
        threading.Thread(target=_clear, daemon=True).start()
        return 1


def triage_ci_failure(repo: str, check_run_id: int, pr_number: int,
                      cfg: dict, out_dir: Path,
                      registry: RunRegistry) -> dict:
    """Unattended run for one failed check run: fetch the failing job's log
    tail, triage it as hostile data (CI logs can carry prompt injections),
    and post the diagnosis on the PR it belongs to."""
    gh = GitHubClient(cfg)
    check = gh.get_check_run(repo, check_run_id)
    log = ""
    try:
        log = gh.get_job_log(repo, check.get("output", {}).get("details_url", "")
                             or check.get("details_url", ""))
    except Exception:
        log = ""  # a private/missing log must not abort: triage on name alone
    case = ReviewCase(kind="ci_failure",
                      title=f"CI failure: {check.get('name', 'unknown check')}",
                      author=check.get("app", {}).get("slug", "") or "actions",
                      description=(check.get("output", {}) or {}).get(
                          "title", "") or "",
                      ci_log=log)
    return run(case, cfg, out_dir, gh=gh, repo=repo, pr_number=pr_number)


def triage_pr(repo: str, number: int, cfg: dict, out_dir: Path,
              registry: RunRegistry) -> dict:
    """Full unattended run for one PR. Records itself in the registry so
    the kill switch can cancel it between pipeline steps."""
    gh = GitHubClient(cfg)
    pr = gh.get_pull(repo, number)
    diff = ""
    try:
        import urllib.request
        req = urllib.request.Request(
            pr["diff_url"], headers={
                "Authorization": f"Bearer {cfg['GITHUB_TOKEN']}",
                "User-Agent": "agentkit-triage/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            diff = resp.read().decode(errors="replace")
    except Exception:
        diff = ""  # triage proceeds on metadata alone; diff is untrusted input
    case = ReviewCase(kind="pull_request", title=pr.get("title", ""),
                      author=pr.get("user", {}).get("login", ""),
                      description=pr.get("body") or "", diff=diff)
    return run(case, cfg, out_dir, gh=gh, repo=repo, pr_number=number)


def make_handler(cfg: dict, out_dir: Path, registry: RunRegistry):
    secret = cfg.get("WEBHOOK_SECRET", "").encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *a):  # quiet; trace is the audit log
            pass

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_check_run(self, payload: dict, cfg: dict,
                              out_dir: Path, registry: RunRegistry) -> None:
            """check_run webhook: only completed+failure with an attached PR
            (first PR only, int-validated) wakes the agent."""
            if payload.get("action") != "completed":
                self._json(200, {"ignored": f"action={payload.get('action')!r}"})
                return
            cr = payload.get("check_run") or {}
            if cr.get("conclusion") != "failure":
                self._json(200, {"ignored": f"conclusion={cr.get('conclusion')!r}"})
                return
            cid = str(cr.get("id", ""))
            if not _INT_RE.match(cid):
                self._json(400, {"error": "bad check_run id"})
                return
            prs = cr.get("pull_requests") or []
            num = str((prs[0] or {}).get("number", "")) if prs else ""
            if not _INT_RE.match(num):
                # failed run with no PR attached (e.g. push build): nothing
                # to comment on — ignore, do not 400 non-PR events
                self._json(200, {"ignored": "no pull request attached"})
                return
            check_run_id, number = int(cid), int(num)

            def work():
                try:
                    res = triage_ci_failure(
                        cfg.get("TARGET_REPO", ""), check_run_id, number,
                        cfg, out_dir, registry)
                except Exception as e:
                    with registry.lock:
                        registry.history.append(
                            {"check_run": check_run_id, "pr": number,
                             "status": "error", "error": repr(e)})
                    return
                with registry.lock:
                    registry.history.append(
                        {"check_run": check_run_id, "pr": number,
                         "status": res["status"], "run_id": res["run_id"],
                         "findings": len(res["findings"])})

            threading.Thread(target=work, daemon=True,
                             name=f"triage-ci-{check_run_id}").start()
            self._json(202, {"accepted": True, "check_run": check_run_id,
                             "pr": number})

        def do_GET(self):
            if self.path == "/health":
                with registry.lock:
                    active = len(registry.active)
                self._json(200, {"ok": True, "active_runs": active})
            elif self.path == "/stop":
                n = registry.kill_all()
                self._json(200, {"killed": n})
            elif self.path == "/runs":
                with registry.lock:
                    self._json(200, {"runs": registry.history[-50:]})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/webhook":
                self._json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 1_000_000:
                self._json(413, {"error": "payload too large"})
                return
            raw = self.rfile.read(length)
            if secret:
                sig = self.headers.get("X-Hub-Signature-256", "")
                want = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig, want):
                    self._json(401, {"error": "bad signature"})
                    return
            try:
                payload = json.loads(raw.decode(errors="replace"))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            event = self.headers.get("X-GitHub-Event", "pull_request")
            # UNTRUSTED payload: extract only validated selectors.
            repo = ((payload.get("repository") or {}).get("full_name") or "")
            if repo != cfg.get("TARGET_REPO", ""):
                self._json(403, {"error": f"refusing repo {repo!r}"})
                return
            if event == "check_run":
                self._handle_check_run(payload, cfg, out_dir, registry)
                return
            if payload.get("action") != "opened":
                self._json(200, {"ignored": f"action={payload.get('action')!r}"})
                return
            num = str((payload.get("pull_request") or {}).get("number", ""))
            if not _INT_RE.match(num):
                self._json(400, {"error": "bad PR number"})
                return
            number = int(num)

            def work():
                try:
                    res = triage_pr(repo, number, cfg, out_dir, registry)
                except Exception as e:
                    with registry.lock:
                        registry.history.append(
                            {"pr": number, "status": "error", "error": repr(e)})
                    return
                with registry.lock:
                    registry.history.append(
                        {"pr": number, "status": res["status"],
                         "run_id": res["run_id"],
                         "findings": len(res["findings"])})

            threading.Thread(target=work, daemon=True,
                             name=f"triage-pr-{number}").start()
            self._json(202, {"accepted": True, "pr": number})

    return Handler


def serve(cfg: dict, out_dir: Path, port: int) -> None:
    registry = RunRegistry(out_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port),
                                make_handler(cfg, out_dir, registry))
    print(f"agentkit trigger server on 127.0.0.1:{port} "
          f"(webhook POST /webhook, kill GET /stop)", flush=True)
    httpd.serve_forever()


def poll(cfg: dict, out_dir: Path, interval: int,
         state_path: Path, once: bool = False) -> None:
    """No-webhook mode for judges: watch TARGET_REPO's open PRs."""
    gh = GitHubClient(cfg)
    repo = cfg.get("TARGET_REPO", "")
    seen: set[int] = set()
    if state_path.exists():
        seen = {int(x) for x in
                state_path.read_text(encoding="utf-8").split() if x.strip()}
    while True:
        try:
            data = gh._req("GET", f"/repos/{repo}/pulls?state=open&per_page=50")
            for pr in data:
                n = pr["number"]
                if n in seen:
                    continue
                print(f"new PR #{n}: {pr.get('title', '')!r}", flush=True)
                res = triage_pr(repo, n, cfg, out_dir, RunRegistry(out_dir))
                print(f"PR #{n}: {res['status']} — "
                      f"{len(res['findings'])} findings", flush=True)
                seen.add(n)
                state_path.write_text(
                    " ".join(str(x) for x in sorted(seen)), encoding="utf-8")
        except Exception as e:
            print(f"poll cycle failed (will retry): {e!r}", flush=True)
        if once:
            return
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentkit.trigger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="webhook + kill-switch HTTP server")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--env", type=Path, default=Path(".env"))
    s.add_argument("--out", type=Path, default=Path("."))
    p = sub.add_parser("poll", help="poll TARGET_REPO open PRs (no webhook)")
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--once", action="store_true")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--out", type=Path, default=Path("."))
    p.add_argument("--state", type=Path, default=Path(".poll_state"))
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        cfg = load_env(args.env)
        (args.out / "traces").mkdir(parents=True, exist_ok=True)
        serve(cfg, args.out, args.port)
        return 0
    cfg = load_env(args.env)
    (args.out / "traces").mkdir(parents=True, exist_ok=True)
    poll(cfg, args.out, args.interval, args.state, args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
