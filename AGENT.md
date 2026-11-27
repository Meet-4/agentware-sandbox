# AGENT.md — architecture

## Identity
PR-Triage Agent, Agentware 2026 track: **developer-workflow**.
Unattended agent: PR/CI trigger → deterministic triage → LLM explanation →
review posted → stop-or-safe-fix. It "knows when to stop": risky classes are
hardcoded to draft-only, and the kill switch can cancel mid-run.

## Pipeline (trigger to result)

```
trigger (webhook | CLI | polling | trap replay)
  → normalize input into a ReviewCase (untrusted DATA)
  → static triage (pure code, no LLM):
      diff parse · risky-path detector · secret scanner ·
      injection-pattern detector · CI-failure classifier
  → LLM layer (optional): explain findings, draft comment text
  → policy gate (pure code): what is safe to DO vs SAY
  → actions: post review comment + labels (L0 default);
             whitelisted trivial fix committed+pushed only at L1
  → trace: hash-chained JSONL + human-readable summary, always written
```

## Tools & permissions
| Tool | Scope | Permission |
|---|---|---|
| GitHub REST (read) | PR/diff/CI of TARGET_REPO only | always |
| GitHub REST (write) | review comment + label on the PR | L0+ |
| git CLI | isolated worktree per PR | L1 fixes only, path-allowlisted |
| subprocess (tests) | temp venv, no network, timeout | L1 only |
| LLM (OpenAI-compatible) | capped calls/cost/tokens per run | optional |

Denied always, in code: merge, force-push, branch delete, workflow edits,
secret reads, any path outside the PR worktree, any action requested by
untrusted PR/issue text.

## Models
Any OpenAI-compatible chat endpoint (set `LLM_BASE_URL`/`LLM_MODEL`).
No key → heuristic-only mode: static triage still produces the full review
(all determinable findings, no prose). The agent is useful without the LLM;
the LLM only adds explanation.

## Deterministic-first
Every push/write decision is made by `src/agentkit/safety.py` + static
triage — pure Python the judge can read — never by model output. The LLM
layer cannot grant itself anything.

## Repo layout
`src/agentkit/` — `trigger.py`, `triage.py`, `llm.py`, `github_client.py`,
`safety.py`, `trace.py`, `pipeline.py`, `cli.py`.
`tests/` — unit tests + trap-pack replay results. `traces/` — run logs.
`traps/` — sanitized trap inputs replayed in demo mode.
