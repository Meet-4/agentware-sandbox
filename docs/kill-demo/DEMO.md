# Kill-switch live demo — real capture, Nov 28 2026, 16:14 IST

Environment: `python -m agentkit.trigger serve --port 8765` + a live poll run
of PR #4 (head a6024ebe4828) started 9 s later. Captured by hand; raw files in
this folder (`20261128-161424-c55ddc.jsonl` = the killed run's trace).

## Sequence (all real)
1. `GET /health` -> `{"ok": true, "active_runs": 0}`
2. Poll run starts: `PR #4 @ a6024ebe4828: 'config: billing-worker retries'`
3. `GET /stop` -> `{"killed": 1}`  (global `traces/KILL` flag written)
4. In-flight run halts BETWEEN pipeline steps: trace ends after
   `static_triage` (6 blockers recorded) — no review/post events follow
5. Trace written anyway (design guarantee: written even on kill);
   `KILL` flag auto-clears after 120 s so later runs proceed

## Tamper-evidence (same capture session)
- `Trace.verify(<real trace>)` -> `ok=True, first_bad_line=None`
- Changed `"decision"` to `"APPROVED"` in line 1 of a copy ->
  `ok=False, first_bad_line=1` (chain breaks at exactly the modified line)

Reproduce: `docs/kill-demo` + `python -m agentkit.trigger serve --help`.
