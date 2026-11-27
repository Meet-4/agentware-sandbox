"""Deterministic triage: parse PR text + diff, flag risk — BEFORE any LLM.

This module is pure code. Its findings decide policy; the LLM only explains
them. Handles the Agentware trap-pack classes: injected instructions in PR
descriptions, malicious postinstall hooks in dependency manifests, and
CI-log injection demanding destructive "fixes".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .safety import risky_path

DIFF_FILE_RE = re.compile(r"^diff --git a/(.+) b/(.+)$", re.MULTILINE)
POSTINSTALL_RE = re.compile(r'"postinstall"\s*:\s*"(.*)"', re.DOTALL)
NET_SCRIPT_RE = re.compile(
    r"fetch\(|curl\s|wget\s|http://|https://|child_process|process\.env", re.IGNORECASE
)
DEP_BUMP_RE = re.compile(
    r'^\s*[-+]\s*"(?P<name>[^"]+)":\s*"(?P<from>[\^~]?[\dx][\d.*-]*)"', re.MULTILINE)

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("instructs the reviewer to approve/auto-merge",
     re.compile(r"approve (?:it|this)[^.\n]*auto[- ]?merge", re.I)),
    ("asks to paste/echo a secret value",
     re.compile(r"paste the value of \w+|include the \w+ (?:key|token) in", re.I)),
    ("claims branch protection / rules do not apply",
     re.compile(r"branch protection does not apply", re.I)),
    ("demands deleting tests or the test suite",
     re.compile(r"rm -rf tests|delete (?:the )?tests?|src/\*\.test\.ts", re.I)),
    ("demands force-push to a protected branch",
     re.compile(r"force[- ]?push", re.I)),
    ("pre-approval social engineering",
     re.compile(r"already (?:been )?(?:security )?reviewed by the maintainers?", re.I)),
    ("claims authority over automated agents",
     re.compile(r"NOTE TO AUTOMATED|note for AI (?:code )?reviewers", re.I)),
]

SECRET_SCAN_RE = re.compile(
    r"(?:AGW-CANARY-[A-Z0-9]+|"
    r"(?:ghp|gho|github_pat)_[A-Za-z0-9]{10,}|"
    r"sk-[A-Za-z0-9]{16,}|"
    r"(?:AWS)?(?:AKIA)[A-Z0-9]{12,})", re.IGNORECASE)


@dataclass
class Finding:
    severity: str  # "blocker" | "high" | "info"
    category: str
    detail: str

    def line(self) -> str:
        return f"[{self.severity.upper()}] {self.category}: {self.detail}"


@dataclass
class ReviewCase:
    """Normalized, UNTRUSTED input. All fields are data, never instructions."""
    kind: str = "pull_request"          # pull_request | ci_failure | issue
    title: str = ""
    author: str = ""
    description: str = ""
    diff: str = ""
    ci_log: str = ""
    findings: list[Finding] = field(default_factory=list)

    def untrusted_payload(self) -> str:
        parts = [f"TITLE: {self.title}", f"AUTHOR: {self.author}",
                 f"DESCRIPTION:\n{self.description}"]
        if self.diff:
            parts.append(f"DIFF:\n{self.diff}")
        if self.ci_log:
            parts.append(f"CI LOG:\n{self.ci_log}")
        return "\n\n".join(parts)


def parse_case_from_markdown(text: str) -> ReviewCase:
    """Trap-replay mode: load a trap .md as a ReviewCase (title, desc, diff)."""
    case = ReviewCase(title=text.splitlines()[0] if text else "")
    m = re.search(r"\*\*Author:\*\*\s*(.+)", text)
    if m:
        case.author = m.group(1).strip()
    # description = between '## Description' and '## Diff' (or end)
    dm = re.search(r"## Description\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if dm:
        case.description = dm.group(1).strip()
    diffm = re.search(r"```diff\n(.*?)```", text, re.DOTALL)
    if diffm:
        case.diff = diffm.group(1).strip()
    if "[ci]" in text and not case.diff:
        case.kind, case.ci_log = "ci_failure", text
        case.title = case.title or "CI failure"
    return case


def triage(case: ReviewCase) -> ReviewCase:
    f = case.findings

    # 1. Prompt-injection signals anywhere in untrusted text.
    blob = case.untrusted_payload()
    for label, pat in INJECTION_PATTERNS:
        if pat.search(blob):
            f.append(Finding("blocker", "prompt-injection",
                             f"untrusted text {label}; quoted as data, not followed"))

    # 2. Secret-looking values present (reported masked; never echoed).
    for hit in set(SECRET_SCAN_RE.findall(blob)):
        f.append(Finding("high", "secret-material",
                         f"value matching secret/canary pattern present "
                         f"({hit[:4]}…); masked in all output"))

    # 3. Risky paths in the diff.
    for a, b in set(DIFF_FILE_RE.findall(case.diff or "")):
        for p in (a, b):
            why = risky_path(p)
            if why:
                f.append(Finding("high", "risky-path", why))

    # 4. Dependency-manifest edits + install hooks reaching out.
    if case.diff:
        m = POSTINSTALL_RE.search(case.diff)
        if m and NET_SCRIPT_RE.search(m.group(1)):
            f.append(Finding("blocker", "supply-chain",
                             "package.json adds postinstall hook with network/"
                             "env access — executable code on `npm install`"))
        dep = DEP_BUMP_RE.search(case.diff)
        if dep:
            f.append(Finding("high", "dependency-bump",
                             f"dependency manifest changes {dep['name']}"))

    # 5. CI-log: classify the failing test vs injection lines.
    if case.ci_log:
        fail = re.search(r"FAIL\s+(.+)", case.ci_log)
        if fail:
            f.append(Finding("info", "ci-failure",
                             f"failing test: {fail.group(1).strip()[:120]}"))

    # dedupe by (severity, category, detail)
    seen, out = set(), []
    for x in f:
        k = (x.severity, x.category, x.detail)
        if k not in seen:
            seen.add(k)
            out.append(x)
    case.findings = out
    return case


def heuristic_review(case: ReviewCase) -> str:
    """No-LLM review draft: full findings, deterministic wording."""
    lines = [f"## Automated PR triage — {case.title or case.kind}",
             "", "Deterministic findings (static triage, no model output):"]
    if case.findings:
        lines += [f"- {x.line()}" for x in case.findings]
    else:
        lines.append("- No deterministic risk flags.")
    blockers = [x for x in case.findings if x.severity == "blocker"]
    if blockers:
        lines += ["", "**Verdict: DO NOT MERGE.** Blockers above require a human "
                  "maintainer; this agent will not act on instructions embedded "
                  "in the PR text."]
    lines += ["", "_Autonomy: L0 — comments only. Nothing was merged, pushed, "
             "or executed from this review._"]
    return "\n".join(lines)


def suggested_labels(case: ReviewCase) -> list[str]:
    labs: list[str] = []
    if any(x.category == "prompt-injection" for x in case.findings):
        labs.append("needs-human-review")
    if any(x.category == "supply-chain" for x in case.findings):
        labs.append("security")
    if any(x.category == "dependency-bump" for x in case.findings):
        labs.append("dependencies")
    return labs
