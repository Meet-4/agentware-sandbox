Webhook smoke run 2026-11-28 ~01:50 IST (real PR https://github.com/Meet-4/agentware-sandbox/pull/1).

- 20261128-015031 (killed): kill switch fired via GET /stop mid-run — run cancelled at first checkpoint, audit entry written (Best Kill Switch demo evidence).
- 20261128-015148 (killed): second webhook landed inside the 120s stop window — also cancelled cleanly.
- 20261128-015310 (ok): full unattended run: webhook accept -> PR+diff fetch -> triage -> LLM commentary -> review comment posted to PR #1 (2nd comment). Trace chain verifies OK.

All three chains verify: python -m agentkit --verify traces/webhook-smoke-2026-11-28/<file>.jsonl
