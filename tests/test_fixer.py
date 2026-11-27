"""L1 fixer tests: pure fix planning + an offline git worktree integration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from agentkit.fixer import (FileFixes, fix_case_mismatch_imports, fix_text,
                            maybe_autofix, plan_fixes, verify_worktree,
                            worktree_for_pr)
from agentkit.safety import KillSwitch, Policy


def test_docs_typo_fixed():
    text, classes = fix_text("README.md", "the teh docs recieve input\n")
    assert "docs-typo" in classes
    assert "the the docs receive input\n" == text


def test_docs_typo_plural_and_case():
    text, _ = fix_text("g.md", "Teh module recieves and Seperates values.\n")
    assert text == "The module receives and Separates values.\n"


def test_lint_fix_trailing_whitespace_and_newline():
    text, classes = fix_text("code.py", "x = 1   \ny = 2")
    assert "lint-fix" in classes
    assert text == "x = 1\ny = 2\n"


def test_no_false_changes():
    text, classes = fix_text("clean.md", "nothing wrong here\n")
    assert classes == [] and text == "nothing wrong here\n"


def test_risky_paths_never_planned():
    plans = plan_fixes({
        ".github/workflows/ci.yml": "teh  ",
        "requirements.txt": "teh ",
        "docs/guide.md": "teh guide recieves",
    })
    # requirements.txt is a dependency manifest — skipped; only docs/ planned
    assert [p.path for p in plans] == ["docs/guide.md"]
    # .txt is in the extension whitelist but requirements.txt is a manifest:
    assert all(p.path != "requirements.txt" for p in plans)


def test_case_mismatch_import():
    files = {"Utils.py": "X = 1\n", "main.py": "import utils\nprint(utils.X)\n"}
    plans = fix_case_mismatch_imports(files)
    assert len(plans) == 1
    assert plans[0].path == "main.py"
    assert "import Utils" in plans[0].fixed
    assert plans[0].classes == ["missing-import"]


def test_case_mismatch_no_change_when_correct():
    files = {"utils.py": "X = 1\n", "main.py": "import utils\n"}
    assert fix_case_mismatch_imports(files) == []


# --- offline git integration -------------------------------------------------

def _init_repo(tmp: Path) -> Path:
    """Bare 'origin' + a clone with one commit on main and a PR-like branch."""
    import subprocess as sp
    origin = tmp / "origin.git"
    sp.run(["git", "init", "--bare", str(origin)], check=True,
           capture_output=True)
    seed = tmp / "seed"
    sp.run(["git", "clone", str(origin), str(seed)], check=True,
           capture_output=True)
    (seed / "README.md").write_text("teh readme recieves data  \n")
    for cmd in (["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t",
                 "commit", "-m", "init"],
                ["git", "push", "origin", "HEAD:refs/heads/main",
                 "HEAD:refs/heads/feature",
                 "HEAD:refs/pull/7/head", "HEAD:refs/pull/9/head"]):
        sp.run(cmd, cwd=str(seed), check=True, capture_output=True)
    return origin


def test_worktree_and_verify_green(tmp_path):
    import subprocess as sp
    origin = _init_repo(tmp_path)
    clone = tmp_path / "clone"
    sp.run(["git", "clone", str(origin), str(clone)], check=True,
           capture_output=True)
    wt, sha = worktree_for_pr(clone, 7, scratch=tmp_path / "wt")
    assert (wt / "README.md").exists() and len(sha) == 40
    # clean tree: only lint fixes planned → apply and gate
    files = {"README.md": (wt / "README.md").read_text()}
    plans = plan_fixes(files)
    assert plans and "lint-fix" in plans[0].classes
    for p in plans:
        (wt / p.path).write_text(p.fixed)
    ok, detail = verify_worktree(wt)
    assert ok, detail


def test_verify_catches_broken_python(tmp_path):
    import subprocess as sp
    origin = _init_repo(tmp_path)
    clone = tmp_path / "clone"
    sp.run(["git", "clone", str(origin), str(clone)], check=True,
           capture_output=True)
    wt, _ = worktree_for_pr(clone, 9, scratch=tmp_path / "wt")
    (wt / "bad.py").write_text("def f(:\n")
    ok, detail = verify_worktree(wt)
    assert not ok and "py_compile failed" in detail


def test_maybe_autofix_gates_on_autonomy(tmp_path):
    class FakeTrace:
        def event(self, *a, **k):
            pass
    res = maybe_autofix(Policy(autonomy="L0"), {"docs/a.md": "teh a"},
                        tmp_path, 1, "feature",
                        KillSwitch(tmp_path), FakeTrace())
    assert res["action"] == "skipped"
    res = maybe_autofix(Policy(autonomy="L1"), {"docs/a.md": "clean\n"},
                        tmp_path, 1, "feature",
                        KillSwitch(tmp_path), FakeTrace())
    assert res["action"] == "none"
