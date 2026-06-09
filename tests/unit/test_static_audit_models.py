from __future__ import annotations

import json
from dataclasses import asdict

from engine.static_audit.models import (
    AgentTrace,
    Claim,
    ClaimMapping,
    EvidenceItem,
    Finding,
    StaticAuditBundle,
    ToolRun,
)
from engine.static_audit.protocol import protocol_manifest
from engine.static_audit.roles import ROLE_DEFINITIONS, role_catalog, skipped_trace


def test_static_audit_bundle_serializes_core_sections(tmp_path) -> None:
    bundle = StaticAuditBundle(
        case_id="case-a",
        inputs={"paper_pdf": "paper.pdf", "source_data_dir": "Source Data"},
        tool_runs=[
            ToolRun(
                tool_id="source_data.findings",
                step_key="source_data_findings",
                status="ran",
                outputs=["source_data_findings.json"],
            )
        ],
        evidence_items=[
            EvidenceItem(
                evidence_id="EV-001",
                kind="cell",
                source_path="source.xlsx",
                locator={"sheet": "Fig.1", "cell": "B2"},
                summary="Original Source Data cell.",
            )
        ],
        claims=[
            Claim(
                claim_id="CL-001",
                text="Example claim",
                claim_type="figure_trace",
                evidence_refs=["EV-001"],
            )
        ],
        findings=[
            Finding(
                finding_id="FD-001",
                category="fixed_difference",
                risk_level="medium",
                summary="Fixed difference candidate.",
                evidence_refs=["EV-001"],
                claim_refs=["CL-001"],
            )
        ],
        claim_mappings=[
            ClaimMapping(
                mapping_id="CM-001",
                claim_id="CL-001",
                evidence_refs=["EV-001"],
                confidence="high",
                finding_refs=["FD-001"],
            )
        ],
        agent_traces=[AgentTrace(role_id="judge", status="ran")],
    )

    path = tmp_path / "static_audit_bundle.json"
    bundle.write_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["case_id"] == "case-a"
    assert data["protocol_version"] == "static_audit_protocol.v1"
    assert data["evidence_items"][0]["kind"] == "cell"
    assert data["execution_status"]["status"] == "not_provided"


def test_protocol_manifest_exposes_ordered_stages() -> None:
    manifest = protocol_manifest()

    assert manifest["protocol_version"] == "static_audit_protocol.v1"
    stage_ids = [item["stage_id"] for item in manifest["stages"]]
    assert stage_ids[0] == "discover_inputs"
    assert "bundle" in stage_ids
    assert "legacy_report" in stage_ids


def test_roles_include_three_real_v1_roles_and_skipped_trace() -> None:
    catalog = role_catalog()
    real_roles = [item["role_id"] for item in catalog if item["real_in_v1"]]

    assert len(catalog) == 8
    assert real_roles == ["claim_extractor", "source_data_auditor", "judge"]

    trace = skipped_trace(ROLE_DEFINITIONS[2])
    assert trace.role_id == "visual_triage"
    assert trace.status == "skipped"


def test_finding_has_issue_category_with_default_consistency() -> None:
    finding = Finding(
        finding_id="FD-001",
        category="fixed_difference",
        risk_level="medium",
        summary="test",
    )
    assert finding.issue_category == "consistency"


def test_finding_accepts_completeness_issue_category() -> None:
    finding = Finding(
        finding_id="COMP-001",
        category="material_missing",
        risk_level="medium",
        summary="test",
        issue_category="completeness",
    )
    assert finding.issue_category == "completeness"


def test_finding_serializes_issue_category() -> None:
    finding = Finding(
        finding_id="COMP-001",
        category="material_missing",
        risk_level="medium",
        summary="test",
        issue_category="completeness",
    )
    data = asdict(finding)
    assert data["issue_category"] == "completeness"
