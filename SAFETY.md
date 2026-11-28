# SAFETY.md — what is enforced IN CODE (not by prompt)

## Input firewall
All PR descriptions, diffs, issue text, and CI log lines are parsed into a
`ReviewCase` and treated strictly as **data**. The LLM prompt embeds them
inside fenced, quoted blocks labelled UNTRUSTED DATA, and the policy gate
(`safety.py`) ignores any "instruction" found in them. Injection patterns
("approve and auto-merge", "paste the value of X", "rm -rf tests/",
"branch protection does not apply to agents") are detected by
`triage.py:injection_signals` and reported as findings — never obeyed.
Demonstrated against the Agentware trap pack (see `tests/`).

## Hard refusals (never configurable at runtime)
- No merge, no force-push, no branch delete, no workflow edits.
- No reads of secret/token env vars, ever; no echo of anything matching the
  secret patterns, canary prefixes included (values are masked in output).
- Writes only inside the per-PR worktree, only via the path allowlist.
- Push only the PR head branch, only L1 whitelisted fix classes.

## Autonomy levels
- **L0 (default)** — observe: post review comments + labels only.
- **L1** — additionally: whitelisted trivial fixes (docs typos, missing
  import, lint) that pass the project's own tests before push.
- Everything else (dependency bumps, logic, CI files) — DRAFT ONLY.

## Budget caps
Per run: max LLM calls, max wall-clock minutes, max USD cost (from `.env`,
defaults 25 calls / 10 min / $0.50). Exceeded → graceful STOP, trace says so.

## Kill switch
- Per-run `traces/<run-id>/KILL` flag file — checked **between every pipeline step**.
- `GET /stop` on the local control port (default 8765) when webhook mode
  runs: writes a global `traces/KILL` flag every live run checks between
  steps; the flag auto-clears after 120 s so the operator does not have to.
- Evidence: `traces/webhook-smoke-2026-11-28/` — a real run killed
  mid-flight via `/stop`, audit entry written, chain verifies.
Either cancels pending work immediately; an audit entry is always written.

## L1 autonomous fix gate (enforced in code, `fixer.py` + `pipeline.py`)
Order of operations for every fix attempt; a failure at any step aborts the
fix path without failing the already-posted review:
1. **Classify** — only three whitelisted fix classes, detected by pure code:
   docs-typo (dictionary, capitalization-preserving, plural-aware),
   lint-fix (trailing whitespace, final newline), missing-import
   (case-mismatch only). Anything else → draft-only.
2. **Isolate** — a fresh git worktree from `refs/pull/N/head`, never the
   working tree; risky paths, `.github/`, manifests and removals/renames are
   never auto-touched.
3. **Gate** — the project's own test suite (compile + pytest, offline,
   timeout) must pass inside the worktree.
4. **Push** — only `HEAD:refs/heads/<PR branch>`, never `--force`, never
   `main`; push happens only after the gate reported OK, still inside the
   budget + kill-switch window.
Evidence: PR #2 (https://github.com/Meet-4/agentware-sandbox/pull/2) — two
fix commits pushed autonomously, zero human input; trace chains verify.

## CI-failure trigger (check_run)
Webhook `check_run` events wake the agent only when action=completed AND
conclusion=failure AND a PR is attached. The failing job's log tail (8 KB)
is fetched and treated as hostile input: injection patterns in log lines are
reported as blockers, never followed. A failed run with no PR (e.g. a push
build) is ignored. Live evidence: PR #3 comment + trace
`traces/20261128-061431-ec3c22` — two injections embedded in a real pytest
failure output both caught.

## Trigger modes (both unattended)
- `python -m agentkit.trigger serve` — webhook server: POST /webhook
  (HMAC-SHA256 verified when WEBHOOK_SECRET is set, repo-gated to
  TARGET_REPO, only the PR number is read from the payload),
  GET /stop (kill), GET /health, GET /runs.
- `python -m agentkit.trigger poll --interval 300` — no public endpoint
  needed (judge mode): polls TARGET_REPO open PRs and triages new ones,
  dedup via an on-disk state file.

## Audit trail
`traces/<run-id>.jsonl` — one event per line, SHA-256 **hash-chained**
(`prev_hash`), written even when the run fails or is killed. Plus a
human-readable `traces/<run-id>.md` summary. Any tampering with an entry
breaks the chain and is detectable by `python -m agentkit verify <trace>`.

## Platform-level enforcement
- `main` is branch-protected at GitHub: force-push **disabled**, branch deletion **disabled**, linear history **required** (verified via API). The no-force-push guarantee is enforced by the platform, not just this document.
