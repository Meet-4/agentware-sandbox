"""SARIF 2.1.0 export: findings as machine-readable results.

CI tooling (GitHub code-scanning, IDEs) speaks SARIF; exporting triage
findings there lets a human drain our output into existing review flows
instead of reading our Markdown. Deterministic and dependency-free —
same findings in, same bytes out.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from .triage import Finding

SARIF_VERSION = "2.1.0"
SCHEMA = ("https://raw.githubusercontent.com/oasis-tcs/sarif-spec/"
          "master/Schemata/sarif-schema-2.1.0.json")

# SARIF level map: blockers are errors, high = warning, info = note.
_LEVEL = {"blocker": "error", "high": "warning", "info": "note"}

# Rules must be stable identifiers: slugify the category, keep the detail
# slug out (details vary per input; rules stay constant).
_RULE_ID = re.compile(r"[^a-z0-9-]+")


def rule_for(category: str) -> str:
    slug = _RULE_ID.sub("-", category.lower()).strip("-")
    return f"AGW{slug.upper()}" if not slug.startswith("AGW") else slug.upper()


def findings_to_sarif(findings: Iterable[Finding], run_id: str = "",
                      source: str = "untrusted-input") -> dict:
    """Convert triage findings into a SARIF 2.1.0 log object."""
    rules: dict[str, dict] = {}
    results = []
    for f in findings:
        rid = rule_for(f.category)
        if rid not in rules:
            rules[rid] = {
                "id": rid,
                "shortDescription": {"text": f.category},
            }
        results.append({
            "ruleId": rid,
            "level": _LEVEL.get(f.severity, "note"),
            "message": {"text": f.detail},
            "locations": [{
                "physicalLocation": {
                    # Findings live in untrusted text, not a repo file:
                    # point at the input, never at our own source.
                    "artifactLocation": {"uri": source},
                },
            }],
            "partialFingerprints": {"agentkitCategory": f.category},
        })
    log = {
        "$schema": SCHEMA,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {"driver": {
                "name": "agentkit",
                "informationUri":
                    "https://github.com/Meet-4/agentware-sandbox",
                "rules": sorted(rules.values(), key=lambda r: r["id"]),
            }},
            "results": results,
        }],
    }
    if run_id:
        log["runs"][0]["automationDetails"] = {
            "id": f"agentkit/run/{run_id}/"}
    return log


def write_sarif(path, findings: Iterable[Finding], run_id: str = "",
                 source: str = "untrusted-input") -> int:
    """Serialize findings to PATH as SARIF; returns the result count."""
    findings = list(findings)
    payload = json.dumps(
        findings_to_sarif(findings, run_id=run_id, source=source),
        indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")
    return len(findings)
