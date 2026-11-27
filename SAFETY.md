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
- `traces/RUN-<id>/KILL` flag file — checked **between every pipeline step**;
- `GET /stop` on the local control port (default 8765) when webhook mode runs.
Either cancels pending work immediately; an audit entry is always written.

## Audit trail
`traces/<run-id>.jsonl` — one event per line, SHA-256 **hash-chained**
(`prev_hash`), written even when the run fails or is killed. Plus a
human-readable `traces/<run-id>.md` summary. Any tampering with an entry
breaks the chain and is detectable by `python -m agentkit verify <trace>`.
