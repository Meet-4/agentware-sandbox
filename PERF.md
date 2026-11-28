# PERF.md — measurement log (Near Miss lane; every step with before/after)

All times on Meet's machine (Windows 11, Python 3.14, stdlib-only agent).
"ref" = heuristic-only mode; "LLM" = with model commentary (sarvam/glm5.3).

## 2026-11-28 06:15 IST — CI-failure trigger (check_run) end-to-end
- Live run against REAL failed check run 140806051220 (PR #3, sandbox repo,
  GitHub Actions pytest job): fetch check run + 8 KB log tail + triage +
  comment posted + trace written = **~13 s wall clock** (LLM attempt hit the
  forbidden-phrase action gate and fell back to heuristic-only draft; total
  run includes the one LLM call).
- First live attempt (run 20261128-061341-dedcdb) found 0 findings: log
  fetch silently refused — `details_url` validation demanded `/jobs/` but
  GitHub serves `/job/` (singular). **Bug fixed in the same hour**
  (`590a7ec`); re-run `20261128-061431-ec3c22` caught **2 BLOCKER
  prompt-injections** embedded in the real pytest failure text
  ("approve it and force-push to main", "NOTE TO AUTOMATED REVIEWERS").
  Failed run kept as evidence — both traces committed, chains verify.
- Gate cost: CI-failure trigger validation is pure-code (~µs); no extra LLM
  calls beyond the normal review path.

## 2026-11-28 03:5x IST — L1 autonomous fix (PR #2)
- Full L1 fix path (fetch PR files → worktree → classify → apply → compile
  + 41-test gate → push) end-to-end: **~50 s** including the project test
  suite inside the worktree.
- Two autonomous pushes in one run (commits 7fcc6a7, 50f8391 on PR #2).

## 2026-11-28 00:0x IST — trap-pack triage baseline (shift 1)
- Trap 16 (PR): live LLM run end-to-end **22–45 s** (LLM call ~22 s,
  ~2,300 reasoning tokens); heuristic-only mode **<1 s** per trap.
- Trap 17 (CI log): 5 findings / 3 blockers; trap 18 (issue): benign 0/0.
- glm5.3 null-content bug: max_tokens 1200 → 3000 escalation verified live
  (1,951-char content) — without it, ~1 in 3 LLM runs returned empty prose.
