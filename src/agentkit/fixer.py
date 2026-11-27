"""L1 auto-fix path: apply whitelisted fix classes to a PR in an ISOLATED
worktree, gate on compile + project tests, push to the PR branch only when
everything is green. Never force, never on the main checkout, never on a
path outside the whitelist.

Fix classes (hard-coded, deterministic, no LLM involvement):
- docs-typo      : fixed-dictionary typos in *.md / README
- lint-fix       : trailing whitespace, missing final newline
- missing-import : case-mismatched local import (import utils vs Utils.py)

Everything else is reported, never auto-fixed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .safety import KillSwitch, Policy, risky_path

TYPO_MAP = {
    "teh": "the", "recieve": "receive", "seperate": "separate",
    "occured": "occurred", "wich": "which", "begining": "beginning",
}
TYPO_RE = re.compile(r"\b(" + "|".join(TYPO_MAP) + r")\b", re.IGNORECASE)


@dataclass
class FileFixes:
    path: str
    original: str
    fixed: str
    classes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.fixed != self.original


def fix_text(name: str, text: str) -> tuple[str, list[str]]:
    """Pure: apply whitelisted textual fixes. Returns (text, classes)."""
    classes: list[str] = []
    base = os.path.basename(name)

    if base.endswith((".md", ".rst")) or base.upper().startswith("README"):
        new = TYPO_RE.sub(lambda m: TYPO_MAP[m.group(0).lower()], text)
        if new != text:
            text, _ = new, classes.append("docs-typo")

    # lint-fix applies to any text file: trailing ws + final newline
    new = "".join(l.rstrip() + "\n" for l in text.splitlines())
    if new != text:
        text = new
        classes.append("lint-fix")
    return text, classes


def plan_fixes(files: dict[str, str]) -> list[FileFixes]:
    """files: {path: full file content}. Returns fix plans for eligible files."""
    plans: list[FileFixes] = []
    for path, content in files.items():
        if risky_path(path) or not path.endswith((".md", ".rst", ".py", ".txt")):
            continue  # protected/unknown file types are never auto-touched
        fixed, classes = fix_text(path, content)
        if classes:
            plans.append(FileFixes(path, content, fixed, classes))
    return plans


def fix_case_mismatch_imports(files: dict[str, str]) -> list[FileFixes]:
    """missing-import: `import foo` where a repo file Foo.py exists (case
    mismatch) — rewrite the import to the real module name."""
    lower_map = {p.lower(): p for p in files if p.endswith(".py")}
    plans: list[FileFixes] = []
    for path, content in files.items():
        if not path.endswith(".py") or risky_path(path):
            continue
        changed = content

        def repl(m: re.Match) -> str:
            mod = m.group(1)
            real = lower_map.get(mod.lower() + ".py")
            if real and real[:-3] != mod:
                return m.group(0).replace(mod, real[:-3])
            return m.group(0)

        new = re.sub(r"^\s*import ([A-Za-z_][\w.]*)", repl, content,
                     flags=re.MULTILINE)
        if new != content:
            plans.append(FileFixes(path, content, new, ["missing-import"]))
    return plans


# --- git plumbing (subprocess, arg lists only, never shell=True) ------------

def _git(cwd: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, timeout=timeout)


def worktree_for_pr(clone_dir: Path, pr_number: int,
                    scratch: Path | None = None) -> tuple[Path, str]:
    """Fetch the PR head ref and materialize an isolated worktree.
    Returns (worktree_path, head_sha). Raises on any git failure."""
    r = _git(clone_dir, "fetch", "origin", f"refs/pull/{pr_number}/head")
    if r.returncode != 0:
        raise RuntimeError(f"git fetch failed: {r.stderr.strip()}")
    sha = _git(clone_dir, "rev-parse", "FETCH_HEAD")
    if sha.returncode != 0:
        raise RuntimeError(f"rev-parse failed: {sha.stderr.strip()}")
    wt = Path(scratch or tempfile.mkdtemp(prefix=f"agw-pr{pr_number}-"))
    wt = wt / f"pr-{pr_number}"
    r = _git(clone_dir, "worktree", "add", "--detach", str(wt), sha.stdout.strip())
    if r.returncode != 0:
        raise RuntimeError(f"worktree add failed: {r.stderr.strip()}")
    return wt, sha.stdout.strip()


def verify_worktree(wt: Path) -> tuple[bool, str]:
    """Compile every .py, then run the project's own test suite if present."""
    python = os.environ.get("PYTHON") or sys.executable
    for py in wt.rglob("*.py"):
        if ".git" in py.parts:
            continue
        c = subprocess.run(
            [python, "-m", "py_compile", str(py)],
            capture_output=True, text=True, timeout=60)
        if c.returncode != 0:
            return False, f"py_compile failed on {py.relative_to(wt)}: {c.stderr[:300]}"
    if (wt / "tests").is_dir() or list(wt.glob("test_*.py")):
        t = subprocess.run(
            [python, "-m", "pytest", "-q", "--no-header"],
            cwd=str(wt), capture_output=True, text=True, timeout=600)
        if t.returncode != 0:
            return False, f"project tests failed:\n{t.stdout[-800:]}{t.stderr[-400:]}"
    return True, "compile ok; tests green (or no test suite)"


def push_worktree(wt: Path, branch: str) -> subprocess.CompletedProcess:
    """Push to the PR branch. NO force ever; branch must already exist."""
    return _git(wt, "push", "origin", f"HEAD:refs/heads/{branch}")


def cleanup_worktree(clone_dir: Path, wt: Path) -> None:
    _git(clone_dir, "worktree", "remove", "--force", str(wt))
    shutil.rmtree(wt.parent, ignore_errors=True)
    _git(clone_dir, "worktree", "prune")


# --- the L1 run ---------------------------------------------------------------

def maybe_autofix(policy: Policy, files: dict[str, str],
                  clone_dir: Path, pr_number: int, pr_branch: str,
                  kill: KillSwitch, trace, scratch: Path | None = None,
                  push: bool = True) -> dict:
    """L1 entry point. Applies whitelisted fixes to the PR branch in an
    isolated worktree, gates on compile+tests, pushes only when green.
    `trace` is the run's Trace logger. Returns a result dict."""
    if policy.autonomy != "L1":
        trace.event("autofix_skipped", reason=f"autonomy={policy.autonomy}",
                    decision="L1 fixes only run at AUTONOMY_LEVEL=L1")
        return {"action": "skipped", "reason": f"autonomy={policy.autonomy}"}

    plans = plan_fixes(files) + fix_case_mismatch_imports(files)
    if not plans:
        trace.event("autofix_skipped", reason="no whitelisted fixable findings",
                    decision="nothing in the fix whitelist applies; human decides")
        return {"action": "none", "reason": "no whitelisted fixes"}

    trace.event("autofix_planned",
                files=[p.path for p in plans],
                classes=[c for p in plans for c in p.classes],
                decision="whitelisted classes only: "
                         + ", ".join(sorted({c for p in plans for c in p.classes})))

    kill.check()
    wt, sha = worktree_for_pr(clone_dir, pr_number, scratch)
    try:
        for p in plans:
            target = wt / p.path
            if not target.exists():
                trace.event("autofix_aborted", reason=f"{p.path} missing in worktree")
                return {"action": "aborted", "reason": f"{p.path} missing"}
            target.write_text(p.fixed, encoding="utf-8")

        _git(wt, "add", "-A")
        msg = ("agent: apply whitelisted fixes ("
               + ", ".join(sorted({c for q in plans for c in q.classes})) + ")"
               + " [autonomous L1]")
        _git(wt, "-c", "user.name=agentkit", "-c",
             "user.email=agentkit@users.noreply.github.com",
             "commit", "-m", msg)

        kill.check()
        ok, detail = verify_worktree(wt)
        trace.event("autofix_verified", gate_ok=ok, detail=detail[:500],
                    decision="push" if ok else "no push — gate failed")
        if not ok:
            return {"action": "gate_failed", "reason": detail}

        if not push:
            return {"action": "planned", "files": [p.path for p in plans]}
        kill.check()
        r = push_worktree(wt, pr_branch)
        if r.returncode != 0:
            trace.event("autofix_push_failed", error=r.stderr[:300])
            return {"action": "push_failed", "reason": r.stderr.strip()[:300]}
        trace.event("autofix_pushed", branch=pr_branch,
                     decision="fixes pushed to PR branch; no force, no merge")
        return {"action": "pushed", "files": [p.path for p in plans],
                "classes": sorted({c for q in plans for c in q.classes})}
    finally:
        cleanup_worktree(clone_dir, wt)
