# agentware-sandbox

**PR-Triage Agent** — an unattended developer-workflow agent (Agentware 2026, track: Developer Workflow).

A pull request (or CI failure) triggers it. It reads the diff, runs a
deterministic security/risk triage **before** any LLM call, drafts a review,
posts it, and — only under an explicitly enabled autonomy level — pushes
whitelisted trivial fixes. Everything else is drafted, never pushed.

## What it does

1. **Trigger** — GitHub webhook (`pull_request` / `check_run`), manual CLI run
   (`python -m agentkit --pr 418`), or a local trap-file replay for testing.
2. **Triage** — deterministic-first: diff parsing, risky-path detector
   (`.github/`, dependency manifests, secrets), secret scanner, and a
   **prompt-injection detector** that quotes untrusted PR text as data and
   never as instructions.
3. **Review draft** — an LLM (any OpenAI-compatible endpoint) explains the
   findings; with no API key the agent still runs in heuristic-only mode.
4. **Post** — review comment + labels to the PR, and a tamper-evident
   hash-chained trace on disk.

## What it does NOT do (honest gaps)

- It does **not** auto-merge, force-push, delete branches, or touch
  `.github/workflows` or secrets — ever; not configurable by prompt.
- It does **not** fix logic bugs or dependency bumps autonomously; those are
  drafted as suggestions only (see AUTONOMY_LEVEL in SAFETY.md).
- Webhook mode requires a publicly reachable endpoint; judges without one
  should use the CLI or polling mode (documented in AGENT.md).
- The LLM layer explains findings; it never gates a push — deterministic
  code does (see SAFETY.md).

## Evidence (live, unattended runs)

- PR triage + posted review: https://github.com/Meet-4/agentware-sandbox/pull/1
  (comments posted via webhook, direct CLI, and polling; trace chains in
  `traces/webhook-smoke-2026-11-28/`).
- **L1 autonomous fix (no human mid-run)**: https://github.com/Meet-4/agentware-sandbox/pull/2 —
  the agent triaged the PR, planned whitelisted fixes (`docs-typo`, `lint-fix`),
  applied them in an isolated git worktree, ran the project's own test suite,
  and pushed only after the gate was green (commits
  `agent: apply whitelisted fixes ... [autonomous L1]`). Trace chain:
  `traces/l1-autofix-2026-11-28/` (verified via `--verify`).

## Quick start (<15 min)

```bash
git clone https://github.com/Meet-4/agentware-sandbox && cd agentware-sandbox
cp .env.example .env      # fill in a GitHub token (repo-scoped) — LLM key optional
python -m agentkit --demo         # bundled trap replay, end-to-end, no GitHub needed
python -m agentkit --pr 418 --repo owner/name   # live triage of a real PR
```

See `AGENT.md` for architecture, `SAFETY.md` for the guardrails enforced
in code, `traces/` for real run logs, `tests/` for trap-pack results.

## License

MIT (see LICENSE).
