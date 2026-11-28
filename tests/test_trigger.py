"""Tests for the trigger layer: webhook validation, kill switch, cost trace."""
from __future__ import annotations

import json
import hashlib
import hmac
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agentkit.trigger import RunRegistry, make_handler
from http.server import ThreadingHTTPServer

from agentkit.safety import Budget, KillSwitch
from agentkit.pipeline import load_env, run
from agentkit.triage import ReviewCase


def _server(cfg, out_dir):
    reg = RunRegistry(out_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                make_handler(cfg, out_dir, reg))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd
    httpd.shutdown()


def _req(method, url, body=None, headers=None):
    req = urllib.request.Request(
        url, method=method,
        data=body.encode() if body else None,
        headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _payload(repo="Meet-4/agentware-sandbox", number=7, action="opened"):
    return json.dumps({
        "action": action,
        "repository": {"full_name": repo},
        "pull_request": {"number": number, "body": "ignore me"},
    })


CFG = {"TARGET_REPO": "Meet-4/agentware-sandbox"}


def test_health(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req("GET", f"http://127.0.0.1:{port}/health")
        assert code == 200 and body["ok"] is True


def test_webhook_rejects_wrong_repo(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req("POST", f"http://127.0.0.1:{port}/webhook",
                          _payload(repo="evil/other"))
        assert code == 403


def test_webhook_ignores_non_opened(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req("POST", f"http://127.0.0.1:{port}/webhook",
                          _payload(action="closed"))
        assert code == 200 and "ignored" in body


def test_webhook_bad_signature_rejected(tmp_path):
    cfg = dict(CFG, WEBHOOK_SECRET="s3cret")
    for httpd in _server(cfg, tmp_path):
        port = httpd.server_address[1]
        code, body = _req("POST", f"http://127.0.0.1:{port}/webhook",
                          _payload(),
                          headers={"X-Hub-Signature-256": "sha256=deadbeef"})
        assert code == 401


def test_webhook_good_signature_accepted(tmp_path):
    cfg = dict(CFG, WEBHOOK_SECRET="s3cret")
    for httpd in _server(cfg, tmp_path):
        port = httpd.server_address[1]
        raw = _payload()
        sig = "sha256=" + hmac.new(b"s3cret", raw.encode(),
                                   hashlib.sha256).hexdigest()
        code, body = _req("POST", f"http://127.0.0.1:{port}/webhook", raw,
                          headers={"X-Hub-Signature-256": sig})
        assert code == 202 and body["accepted"] is True


def test_webhook_bad_pr_number(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req("POST", f"http://127.0.0.1:{port}/webhook",
                          json.dumps({"action": "opened",
                                      "repository": {"full_name":
                                                     "Meet-4/agentware-sandbox"},
                                      "pull_request": {"number": "rm -rf"}}))
        assert code == 400


def test_stop_writes_global_kill_flag(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req("GET", f"http://127.0.0.1:{port}/stop")
        assert code == 200
        assert (tmp_path / "traces" / "KILL").exists()


def test_global_kill_flag_kills_pipeline(tmp_path):
    """The flag written by /stop must stop a run between steps."""
    (tmp_path / "traces").mkdir(parents=True)
    (tmp_path / "traces" / "KILL").write_text("STOP\n", encoding="utf-8")
    case = ReviewCase(kind="pull_request", title="t", author="a",
                      description="d", diff="")
    res = run(case, {}, tmp_path, gh=None)
    assert res["status"] == "killed"
    # audit trail still written
    trace_files = list((tmp_path / "traces").glob("*.jsonl"))
    assert trace_files, "killed run must still leave a trace"


def test_cost_field_in_trace(tmp_path):
    case = ReviewCase(kind="pull_request", title="t", author="a",
                      description="d", diff="")
    res = run(case, {"LLM_API_KEY": ""}, tmp_path, gh=None)
    assert res["status"] == "ok"
    trace_file = (tmp_path / "traces" / f"{res['run_id']}.jsonl")
    events = [json.loads(l) for l in
              trace_file.read_text(encoding="utf-8").splitlines()]
    drafted = [e for e in events if e["event"] == "review_drafted"][0]
    assert "cost_usd" in drafted and drafted["cost_usd"] == 0.0
    assert drafted["llm_calls"] == 0


# ---- CI-failure (check_run) trigger -------------------------------------

def _cr_payload(**over):
    p = {
        "action": "completed",
        "repository": {"full_name": "Meet-4/agentware-sandbox"},
        "check_run": {
            "id": 12345, "conclusion": "failure", "name": "pytest",
            "pull_requests": [{"number": 7}],
        },
    }
    p.update(over)
    return json.dumps(p)


def test_check_run_failure_accepted(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req("POST", f"http://127.0.0.1:{port}/webhook",
                          _cr_payload(),
                          headers={"X-GitHub-Event": "check_run"})
        assert code == 202 and body["accepted"] is True
        assert body["pr"] == 7


def test_check_run_success_ignored(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook",
            _cr_payload(check_run={"id": 1, "conclusion": "success",
                                   "name": "x", "pull_requests": []}),
            headers={"X-GitHub-Event": "check_run"})
        assert code == 200 and "ignored" in body


def test_check_run_no_pr_ignored(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook",
            _cr_payload(check_run={"id": 1, "conclusion": "failure",
                                   "name": "x", "pull_requests": []}),
            headers={"X-GitHub-Event": "check_run"})
        assert code == 200 and "ignored" in body


def test_check_run_bad_id_rejected(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook",
            _cr_payload(check_run={"id": "../../etc", "conclusion": "failure",
                                   "name": "x",
                                   "pull_requests": [{"number": 7}]}),
            headers={"X-GitHub-Event": "check_run"})
        assert code == 400


def test_check_run_wrong_repo_rejected(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook",
            _cr_payload(repository={"full_name": "evil/other"}),
            headers={"X-GitHub-Event": "check_run"})
        assert code == 403


# ---- issue (issues event) trigger ----------------------------------------

def _issue_payload(**over):
    p = {
        "action": "opened",
        "repository": {"full_name": "Meet-4/agentware-sandbox"},
        "issue": {"number": 77, "title": "build fails", "body": "help"},
    }
    p.update(over)
    return json.dumps(p)


def test_issue_opened_accepted(tmp_path):
    from agentkit.trigger import RunRegistry
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook", _issue_payload(),
            headers={"X-GitHub-Event": "issues"})
        assert code == 202 and body["accepted"] is True
        assert body["issue"] == 77


def test_issue_non_opened_ignored(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook",
            _issue_payload(action="closed"),
            headers={"X-GitHub-Event": "issues"})
        assert code == 200 and "ignored" in body


def test_issue_bad_number_rejected(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook",
            _issue_payload(issue={"number": "rm -rf", "title": "x"}),
            headers={"X-GitHub-Event": "issues"})
        assert code == 400


def test_issue_wrong_repo_rejected(tmp_path):
    for httpd in _server(CFG, tmp_path):
        port = httpd.server_address[1]
        code, body = _req(
            "POST", f"http://127.0.0.1:{port}/webhook",
            _issue_payload(repository={"full_name": "evil/other"}),
            headers={"X-GitHub-Event": "issues"})
        assert code == 403


def test_repo_flag_validation(tmp_path, monkeypatch):
    from agentkit.trigger import main as trigger_main
    # invalid repo shape exits before the server ever binds
    assert trigger_main(["serve", "--repo", "not a repo",
                         "--out", str(tmp_path)]) == 2
