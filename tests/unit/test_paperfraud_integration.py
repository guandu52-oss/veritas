from __future__ import annotations

import json

from engine.static_audit.tools.paperfraud_rules import (
    paperfraud_findings_from_matches,
    run_paperfraud_rule_match,
)
from engine.static_audit.orchestrator import collect_claims_and_findings
from engine.tools.registry import (
    STATIC_AUDIT_V1_TOOL_IDS,
    tool_catalog_for_agent,
)


def test_paperfraud_rule_match_writes_artifact_and_reviewer_form(tmp_path) -> None:
    full_md = tmp_path / "full.md"
    full_md.write_text(
        """
        # Example clinical study

        We conducted a randomized controlled trial with a reported sample size.
        The methods describe p-value thresholds, statistical significance, and
        multiple comparisons in the analysis plan.
        """,
        encoding="utf-8",
    )
    output_path = tmp_path / "paperfraud_rule_matches.json"

    artifact = run_paperfraud_rule_match(full_md, output_path)
    data = json.loads(output_path.read_text(encoding="utf-8"))

    assert artifact["summary"]["total_rules_loaded"] >= 40
    assert data["summary"]["total_triggered"] > 0
    assert data["triggered_rules"][0]["rule_id"]
    assert len(data["reviewer_form"]) == data["summary"]["total_rules_loaded"]


def test_paperfraud_matches_convert_to_canonical_findings(tmp_path) -> None:
    full_md = tmp_path / "full.md"
    full_md.write_text(
        "This randomized controlled trial reports p-value significance without effect size.",
        encoding="utf-8",
    )
    artifact = run_paperfraud_rule_match(full_md, tmp_path / "paperfraud_rule_matches.json")

    findings = paperfraud_findings_from_matches(artifact)

    assert findings
    assert findings[0].finding_id.startswith("PF-")
    assert findings[0].category.startswith("paperfraud.")
    assert findings[0].manual_review_note
    assert findings[0].metadata["source_artifact"] == "paperfraud_rule_matches.json"


def test_paperfraud_rule_match_registered_in_static_audit_catalog() -> None:
    catalog_ids = {tool["tool_id"] for tool in tool_catalog_for_agent()}

    assert "paperfraud.rule_match" in STATIC_AUDIT_V1_TOOL_IDS
    assert "paperfraud.rule_match" in catalog_ids


def test_paperfraud_rule_matches_merge_into_canonical_findings(tmp_path) -> None:
    full_md = tmp_path / "full.md"
    full_md.write_text("A cohort study reports p-value significance without effect size.", encoding="utf-8")
    run_paperfraud_rule_match(full_md, tmp_path / "paperfraud_rule_matches.json")

    _claims, _mappings, findings = collect_claims_and_findings(tmp_path, [])

    assert any(finding.finding_id.startswith("PF-") for finding in findings)
