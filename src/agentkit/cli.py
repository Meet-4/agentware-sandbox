"""CLI entry: python -m agentkit [--pr N --repo owner/name] [--trap FILE]
[--demo] [--verify TRACE.jsonl]

--demo replays the bundled trap pack (developer-workflow inputs) end-to-end
with no GitHub token required — the judge-visible unattended run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .github_client import GitHubClient
from .pipeline import load_env, run, run_trap_file
from .trace import Trace
from .triage import ReviewCase


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentkit")
    ap.add_argument("--pr", type=int, help="PR number to triage live")
    ap.add_argument("--repo", help="owner/name (must equal TARGET_REPO)")
    ap.add_argument("--trap", type=Path, help="replay a trap .md/.txt file")
    ap.add_argument("--demo", action="store_true", help="run bundled trap pack")
    ap.add_argument("--scan-dir", type=Path, dest="scan_dir",
                    help="cross-track firewall scan: treat every file under "
                         "DIR as raw untrusted input (heuristic-only, dry-run)")
    ap.add_argument("--verify", type=Path, help="verify a trace hash-chain")
    ap.add_argument("--sarif", type=Path, dest="sarif_out",
                    help="also write findings of this run to PATH as a "
                         "SARIF 2.1.0 log (machine-readable output)")
    ap.add_argument("--env", type=Path, default=Path(".env"))
    ap.add_argument("--out", type=Path, default=Path("."))
    args = ap.parse_args(argv)

    def dump_sarif(findings, run_id=""):
        """Write SARIF next to the other run outputs (best-effort)."""
        if not args.sarif_out:
            return
        from .sarif import write_sarif
        n = write_sarif(args.sarif_out, findings, run_id=run_id,
                        source=str(args.trap or args.scan_dir or "input"))
        print(f"sarif: {n} results -> {args.sarif_out}")


    if args.verify:
        ok, bad = Trace.verify(args.verify)
        print(f"trace chain: {'OK' if ok else f'BROKEN at line {bad}'}")
        return 0 if ok else 1

    cfg = load_env(args.env)

    if args.demo:
        trap_dir = Path(__file__).resolve().parents[2] / "traps"
        results = []
        for md in sorted(trap_dir.glob("*.md")) + sorted(trap_dir.glob("*.txt")):
            res = run_trap_file(md, cfg, args.out)
            results.append(res)
            print(f"{md.name}: {res['status']} — "
                  f"{len(res['findings'])} findings "
                  f"({sum(1 for f in res['findings'] if f.severity == 'blocker')} blockers)")
        dump_sarif([f for r in results for f in r["findings"]])
        return 0 if all(r["status"] == "ok" for r in results) else 1

    if args.scan_dir:
        from .pipeline import scan_raw_file
        files = sorted(p for p in args.scan_dir.rglob("*") if p.is_file())
        if not files:
            print(f"no files under {args.scan_dir}")
            return 1
        # heuristic-only: strip LLM keys so the cross-track sweep is
        # deterministic, fast and free — findings come from pure code.
        cfg = {k: v for k, v in cfg.items() if not k.startswith("LLM_")}
        total = blockers = 0
        all_findings: list = []
        for f in files:
            res = scan_raw_file(f, cfg, args.out)
            all_findings.extend(res["findings"])
            n_b = sum(1 for x in res["findings"] if x.severity == "blocker")
            total += len(res["findings"])
            blockers += n_b
            print(f"{f.relative_to(args.scan_dir)}: {res['status']} — "
                  f"{len(res['findings'])} findings ({n_b} blockers)")
        dump_sarif(all_findings)
        print(f"TOTAL: {len(files)} files, {total} findings, {blockers} blockers")
        return 0

    if args.trap:
        res = run_trap_file(args.trap, cfg, args.out)
        dump_sarif(res["findings"], run_id=res["run_id"])
        print(res["review"] or f"status={res['status']}")
        return 0 if res["status"] == "ok" else 1

    if args.pr:
        repo = args.repo or cfg.get("TARGET_REPO", "")
        gh = GitHubClient(cfg)
        pr = gh.get_pull(repo, args.pr)
        case = ReviewCase(
            kind="pull_request", title=pr.get("title", ""),
            author=pr.get("user", {}).get("login", ""),
            description=pr.get("body") or "", diff="")
        # fetch the diff (text/plain patch)
        import urllib.request
        req = urllib.request.Request(
            pr["diff_url"],
            headers={"Authorization": f"Bearer {cfg['GITHUB_TOKEN']}",
                     "User-Agent": "agentkit-triage/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            case.diff = resp.read().decode(errors="replace")
        res = run(case, cfg, args.out, gh=gh, repo=repo, pr_number=args.pr,
                  pr_head=pr.get("head", {}).get("ref"),
                  clone_dir=Path.cwd())
        dump_sarif(res["findings"], run_id=res["run_id"])
        print(res["review"] or f"status={res['status']}")
        return 0 if res["status"] == "ok" else 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
