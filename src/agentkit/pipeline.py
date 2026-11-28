"""Pipeline: trigger → triage → review → post → trace, kill-switch checked
between every step, budget-capped, trace written even on failure/kill."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from .fixer import maybe_autofix
from .github_client import GitHubClient
from .llm import compose_review
from .safety import (Budget, KillSwitch, Policy, gate_action_text,
                     sanitize_review)
from .triage import ReviewCase, parse_case_from_markdown, suggested_labels, triage
from .trace import Trace


def load_env(path: Path) -> dict:
    cfg: dict = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def run(case: ReviewCase, cfg: dict, out_dir: Path,
        gh: GitHubClient | None, repo: str | None = None,
        pr_number: int | None = None, pr_head: str | None = None,
        clone_dir: Path | None = None) -> dict:
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    trace = Trace(run_id=run_id, out_dir=out_dir)
    policy = Policy(autonomy=cfg.get("AUTONOMY_LEVEL", "L0"))
    budget = Budget(
        max_llm_calls=int(cfg.get("MAX_LLM_CALLS_PER_RUN", 25)),
        max_minutes=float(cfg.get("MAX_RUN_MINUTES", 10)),
        max_cost_usd=float(cfg.get("MAX_COST_USD_PER_RUN", 0.5)))
    kill = KillSwitch(out_dir / "traces" / run_id)
    budget.start()
    status = "ok"
    review = ""
    try:
        kill.check()
        trace.event("run_start", case_kind=case.kind,
                    input_digest=len(case.untrusted_payload()),
                    autonomy=policy.autonomy)

        kill.check()
        case = triage(case)
        trace.event("static_triage", findings=[f.line() for f in case.findings],
                    decision="findings recorded; untrusted text kept as data")

        kill.check()
        review = compose_review(case, budget, cfg)
        review = sanitize_review(review)
        ok, hit = gate_action_text(review)
        if not ok:
            review = compose_review(case, Budget(0, 0, 0), {})  # heuristic-only
            review = sanitize_review(review)
            trace.event("action_gate", blocked=hit,
                        decision="LLM text contained a forbidden phrase; "
                                 "replaced with heuristic-only review")
        trace.event("review_drafted", decision="comment text ready",
                    llm_used=bool(cfg.get("LLM_API_KEY")),
                    cost_usd=budget._cost,
                    llm_calls=budget._llm_calls)

        labels = suggested_labels(case)
        kill.check()
        if gh and repo and pr_number is not None and cfg.get("GITHUB_TOKEN"):
            gh.post_comment(repo, pr_number, review)
            if labels:
                gh.add_labels(repo, pr_number, labels)
            trace.event("posted", repo=repo, pr=pr_number, labels=labels)
        else:
            trace.event("dry_run", decision="no token/repo — review kept local",
                        labels=labels)

        # L1 auto-fix path (post-review, still inside budget + kill switch)
        fix_result = None
        if (policy.autonomy == "L1" and gh and repo and pr_number is not None
                and pr_head and cfg.get("GITHUB_TOKEN")):
            try:
                files: dict[str, str] = {}
                for f in (gh.list_pr_files(repo, pr_number) or []):
                    name = f.get("filename", "")
                    if f.get("status") not in ("added", "modified"):
                        continue  # removals/renames are never auto-touched
                    files[name] = gh.get_file_content(repo, name, pr_head)
                fix_result = maybe_autofix(
                    policy, files, clone_dir or Path.cwd(), pr_number,
                    pr_head, kill, trace, scratch=out_dir / ".wt-scratch")
            except Exception as e:  # fix failure never fails the run
                trace.event("autofix_error", error=repr(e)[:300],
                            decision="review already delivered; fix path aborted")

        trace.event("run_end", status="ok",
                    blockers=sum(1 for f in case.findings
                                  if f.severity == "blocker"),
                    autofix=(fix_result or {}).get("action", "n/a"))
        trace.summary(
            f"## Run {run_id} — {case.title}\n"
            f"- Input: {case.kind} from {case.author or 'unknown'} (UNTRUSTED, quoted as data)\n"
            f"- Findings: {len(case.findings)} "
            f"({sum(1 for f in case.findings if f.severity == 'blocker')} blockers)\n"
            f"- Actions: review drafted{' and posted' if gh and repo and pr_number is not None else ' (dry run)'}; "
            f"no merge/push/execution of PR content\n"
            f"- Cost: ${trace.cost()} — LLM calls: {budget._llm_calls}")
    except KeyboardInterrupt as e:
        status = "killed"
        trace.event("killed", reason=str(e),
                    note="kill switch fired; audit entry written on stop")
    except Exception as e:
        status = "error"
        trace.event("error", error=repr(e))
    return {"run_id": run_id, "status": status, "findings": case.findings,
            "review": review if status == "ok" else ""}


def run_trap_file(md_path: Path, cfg: dict, out_dir: Path) -> dict:
    case = parse_case_from_markdown(md_path.read_text(encoding="utf-8"))
    return run(case, cfg, out_dir, gh=None)


def scan_raw_file(path: Path, cfg: dict, out_dir: Path) -> dict:
    """Cross-track firewall scan: the file's ENTIRE text is untrusted data.

    Used for the Agentware cross-track trap inputs (.eml/.html/.json/.csv/.txt
    from inbox, life-admin, research, browser and open tracks). The agent is
    a developer-workflow agent, so it takes no calendar/life-admin ACTION on
    these — the point of the scan is to prove the input firewall holds on
    formats it was never designed for: injections land in findings as data,
    secrets are masked, and the run ends dry (nothing posted).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    case = ReviewCase(
        kind="raw_input",
        title=f"cross-track input {path.name}",
        description=text)
    return run(case, cfg, out_dir, gh=None)
