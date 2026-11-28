# Rubric self-audit — 2026-11-28 10:45 IST (shift 6)

Honest scoring of the submission as it stands TODAY, criterion by criterion,
against the published weights (A30 / U25 / R20 / E15 / D10, safety +4 on top).
Evidence cited for every claim; gaps named, not hidden. Judges: this file is
our own audit — the traces and PRs are the receipts.

## Autonomy 30% — strongest axis
- PR #1 triaged and commented via three paths with zero human input
  (direct CLI, webhook, poll): traces/webhook-smoke-2026-11-28/.
- CI failures: agent wakes on `check_run` failure, fetches the real job log,
  posts diagnosis on the PR unattended — proven BOTH via direct invocation
  (traces/20261128-061431-ec3c22) and through the live webhook server
  (traces/webhook-checkrun-fire-2026-11-28/RESULTS.md, ~35 s end-to-end).
- L1 autonomous fix: PR #2 — isolated worktree, whitelisted fixes, test gate,
  agent-pushed commits (traces/l1-autofix-2026-11-28/).
- Poll mode = full unattended loop for judges without webhook infrastructure
  (`python -m agentkit.trigger poll`).
- GAP: demo video (the scored unattended-run proof) not yet recorded —
  without it the trace is the only autonomy evidence. Meet records per
  docs/demo-script.md; biggest remaining deliverable.

## Usefulness 25%
- Real job: PR security triage + CI-failure first response, the exact task
  maintainers hand off on busy mornings. Runs on any repo with a scoped
  token; heuristic-only mode needs zero LLM keys.
- GAP: single-repo scope by design (honest limit, stated in README).

## Reliability 20% — heavily evidenced
- All 20 trap-pack inputs (every track) fenced: 32 findings, 13 blockers,
  0 crashes, 0 actions taken, canary token never echoed
  (tests/trap_pack/cross-track/RESULTS.md).
- Live failure/recovery evidence: the `/job/` vs `/jobs/` details_url bug
  caught on a real run, fixed, re-run green (kept both traces).
- 36/36 unit tests; negative controls live (tampered HMAC → 401,
  no-PR payload ignored).

## Engineering 15%
- stdlib-only Python 3.14, no agent framework; modules ≤ ~300 lines each;
  strict input validation everywhere (int-regex ids, repo gating, URL allow-list).
- Stranger-runnable: README quick start < 15 min, .env.example documents every
  key + tier, MIT license, public repo.
- Hash-chained JSONL traces with --verify (audit-trail bonus lane).

## Demo 10%
- GAP: not yet recorded. Script complete (docs/demo-script.md) with four
  real evidence segments ready to replay: poll run, trap refusal, kill
  switch mid-run (recorded live in shift 2), PR #2 autonomous fix,
  PR #3 CI diagnosis. ≤ 3 min constraint accounted for.

## Safety bonus +4
- Guardrails +1: destructive actions blocked IN CODE (hard refusals in
  safety.py; push never --force, never to main).
- Live monitoring +1: /health + /runs endpoints (run registry).
- Kill switch +1: GET /stop cancels pending work at the next checkpoint,
  proven live mid-run with audit entry (shift 2 traces).
- Audit trail +1: hash-chained traces, chain verified on every evidence run.

## Bottom line
Code and evidence are freeze-ready except ONE deliverable: the ≤3-min demo
video (Meet records; script + checklist ready). Submission-form URL still
needed from Discord/email relay. Remaining hours before freeze should go to:
(1) demo recording, (2) final anatomy re-check, (3) no risky code churn.
