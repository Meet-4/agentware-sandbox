# Demo video script (≤3 min) — one unattended run, no human on keyboard

Record with OBS; total ≤ 180 s. Segments use REAL runs that already happened
(replay = open the linked PR + tail the trace file, so every claim on screen
is verifiable). Voiceover notes in italics; on-screen actions in bullets.

## 0:00–0:20 — What it is (no screen action)
- Title card: PR-Triage Agent — Agentware 2026, developer-workflow track.
- *One sentence: "An unattended agent that watches a repo: every PR opened
  and every CI failure triggers a full triage run — and it knows when to
  stop."*

## 0:20–1:00 — LIVE unattended run (the required segment — record fresh)
- Terminal split: `python -m agentkit.trigger poll --once` on the left,
  `gh api .../pulls` timeline (or the repo PR page) on the right.
- Open a new PR in the browser (pre-prepared branch) → the agent wakes with
  no human input → shows: found PR → triaged → findings → comment appears
  on the PR. *Point at the trace path printed.*
- On screen, highlight the trace: hash-chained JSONL written even though
  nothing crashed.

## 1:00–1:40 — It refuses hostile instructions (trap pack)
- `python -m agentkit --demo` (trap replay): show trap 16 (PR with
  injection) and 17 (CI log demanding rm -rf / force-push).
- *One line: "Injected instructions are quoted as data and reported as
  blockers — never followed."* Show the DO NOT MERGE verdict.

## 1:40–2:20 — Kill switch (mid-run, one action)
- Start a run, hit `GET /stop` in the browser/terminal → run cancels at the
  next checkpoint, "killed" audit entry appears in the trace, chain still
  verifies.
- *One line: "One action cancels everything in flight; the audit trail is
  still written."*

## 2:20–3:00 — Autonomy beyond comments (already-live evidence)
- PR #2 (github.com/Meet-4/agentware-sandbox/pull/2): agent's own commits
  pushed after a green test gate — scroll the commit list + trace dir
  `traces/l1-autofix-2026-11-28/`.
- PR #3 CI-failure diagnosis: two prompt injections caught inside a REAL
  failing Actions run, comment posted unattended
  (`traces/20261128-061431-ec3c22`).
- Close: safety properties in one sentence — "No merge, no force-push, no
  secrets, budget-capped, killable, everything audited." Repo URL card.

## Recording checklist
- [ ] Fresh poll run recorded live (not paused cuts)
- [ ] Kill-switch segment shows the killed trace event
- [ ] Total time check ≤ 3:00
- [ ] No secrets/token visible anywhere (check terminal scrollback!)
- [ ] No human name but Meet's if any; agent uncredited per house rule
