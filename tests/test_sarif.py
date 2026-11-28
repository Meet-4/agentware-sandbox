"""SARIF 2.1.0 export: schema shape, level mapping, determinism."""
from pathlib import Path

from agentkit.sarif import findings_to_sarif, rule_for, write_sarif
from agentkit.triage import Finding


def _findings():
    return [
        Finding("blocker", "prompt-injection", "untrusted text asks to exfiltrate"),
        Finding("high", "risky-dependency", "postinstall runs curl"),
        Finding("info", "secret-hint", "mentions .env"),
        Finding("blocker", "prompt-injection", "second injection hit"),
    ]


def test_level_mapping():
    log = findings_to_sarif(_findings())
    results = log["runs"][0]["results"]
    assert [r["level"] for r in results] == [
        "error", "warning", "note", "error"]


def test_rules_dedup_and_are_constant_ids():
    log = findings_to_sarif(_findings())
    rules = log["runs"][0]["tool"]["driver"]["rules"]
    ids = [r["id"] for r in rules]
    assert len(ids) == len(set(ids)) == 3
    assert all(id.startswith("AGW") for id in ids)


def test_rule_id_stable_and_slugified():
    assert rule_for("prompt-injection") == "AGWPROMPT-INJECTION"
    assert rule_for("risky dependency!") == "AGWRISKY-DEPENDENCY"
    # deterministic across calls
    assert rule_for("x") == rule_for("x")


def test_schema_headers():
    log = findings_to_sarif(_findings())
    assert log["version"] == "2.1.0"
    assert log["$schema"].endswith("sarif-schema-2.1.0.json")
    assert log["runs"][0]["tool"]["driver"]["name"] == "agentkit"


def test_locations_point_at_input_not_our_source():
    results = findings_to_sarif(_findings(), source="traps/02.eml")["runs"][0]["results"]
    for r in results:
        loc = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert loc == "traps/02.eml"


def test_automation_details_carry_run_id():
    log = findings_to_sarif(_findings(), run_id="20261128-220000-abcd12")
    assert log["runs"][0]["automationDetails"]["id"] == \
        "agentkit/run/20261128-220000-abcd12/"


def test_deterministic_bytes(tmp_path: Path):
    a = tmp_path / "a.sarif"
    b = tmp_path / "b.sarif"
    n = write_sarif(a, _findings(), run_id="r1")
    write_sarif(b, _findings(), run_id="r1")
    assert n == 4
    assert a.read_bytes() == b.read_bytes()


def test_empty_findings_produce_valid_empty_run():
    log = findings_to_sarif([])
    assert log["runs"][0]["results"] == []
    assert log["runs"][0]["tool"]["driver"]["rules"] == []


def test_no_secrets_in_serialization():
    # details are findings text — masking is upstream (safety), but the
    # serialized log must round-trip through json cleanly.
    log = findings_to_sarif([Finding("info", "x", 'quote " and \\ slash')])
    import json
    assert json.loads(json.dumps(log))["runs"][0]["results"][0][
        "message"]["text"] == 'quote " and \\ slash'
