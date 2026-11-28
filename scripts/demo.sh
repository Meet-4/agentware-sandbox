#!/usr/bin/env bash
# One-command judge demo: everything the rubric asks to see, in ~3 minutes.
#
#   bash scripts/demo.sh
#
# No GitHub token or LLM key required — this exercises the deterministic
# paths (trap replay + cross-track firewall + trace verification + SARIF).
# With a token in .env, add --pr N --repo owner/name for the live path.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python

OUT=${DEMO_OUT:-build-demo-out}
mkdir -p "$OUT"

echo "== [0/5] environment =="
"$PY" --version
test -f .env || echo "note: no .env — heuristic-only mode (fine for demo)"

echo
echo "== [1/5] install (editable) =="
"$PY" -m pip install -e . -q

echo
echo "== [2/5] unit tests (safety gates, triage, fixer, trigger, SARIF) =="
"$PY" -m pytest -q

echo
echo "== [3/5] bundled trap replay: end-to-end, unattended =="
"$PY" -m agentkit --demo --out "$OUT" --sarif "$OUT/findings.sarif"

echo
echo "== [4/5] trace hash-chain verification (audit trail) =="
FAIL=0
for t in "$OUT"/traces/*.jsonl; do
  "$PY" -m agentkit --verify "$t" || FAIL=1
done
test "$FAIL" -eq 0

echo
echo "== [5/5] kill switch: flip the switch, confirm a live run halts =="
mkdir -p "$OUT/traces"
echo stop > "$OUT/traces/KILL"
"$PY" - <<PYEOF
from pathlib import Path
from agentkit.pipeline import load_env, run_trap_file
res = run_trap_file(Path("traps/16-pull-request.md"), load_env(Path(".env")) if Path(".env").exists() else {}, Path("$OUT"))
print("status with KILL set:", res["status"], "(expected: killed)")
assert res["status"] == "killed", "kill switch did not halt the run"
PYEOF
rm "$OUT/traces/KILL"

echo
echo "ALL GREEN. Outputs in $OUT/ (traces/, findings.sarif)."
echo "Live path (needs token): python -m agentkit --pr N --repo owner/name"
