from __future__ import annotations

import json

from engine.static_audit.html_report import render_static_audit_html


def write_json(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_static_audit_html_report_renders_priority_evidence_card(tmp_path) -> None:
    write_json(
        tmp_path / "source_data_findings.json",
        {
            "summary": {"priority_findings": 1, "claim_to_source_data_mappings": 1},
            "priority_findings": [
                {
                    "finding_id": "F-TEST-001",
                    "category": "fixed_difference",
                    "risk_level": "medium",
                    "workbook": "case_source.xlsx",
                    "sheet": "Source Data Fig.2",
                    "column_pair": ["D", "E"],
                    "overlap_rows": 35,
                    "support_rows": 35,
                    "support_rate": 1.0,
                    "relationship_value": "0.3",
                    "sample_pairs": [{"row": 5, "left": "0.45", "right": "0.15"}],
                    "benign_explanations": ["formula-derived column"],
                }
            ],
            "claim_to_source_data": [
                {
                    "mapping_id": "CM-TEST-001",
                    "source_figure_id": "Fig.2",
                    "linked_priority_findings": [{"finding_id": "F-TEST-001"}],
                    "candidate_claims": [{"text": "Treatment changes the measured endpoint."}],
                    "matched_paper_references": [
                        {"line_start": 729, "line_end": 730, "match_label": "Fig. 2"}
                    ],
                }
            ],
        },
    )
    write_json(tmp_path / "source_data_profile.json", {"summary": {"workbook_count": 1}})
    write_json(tmp_path / "static_audit_bundle.json", {"agent_traces": [], "claim_mappings": [1]})
    write_json(tmp_path / "agent_judge.json", {"summary": {"technical_risk_summary": "Review needed."}})

    html = render_static_audit_html(tmp_path, "case-a")

    assert "final_audit_report.html" not in html
    assert "F-TEST-001" in html
    assert "full.md:729-730" in html
    assert "case_source.xlsx" in html
    assert "formula-derived column" in html


def test_static_audit_html_report_pattern_view_is_case_agnostic(tmp_path) -> None:
    write_json(
        tmp_path / "source_data_pair_forensics.json",
        {
            "summary": {"priority_findings": 2},
            "priority_findings": [
                {
                    "finding_id": "GEN-ROW-001",
                    "category": "row_offset_scalar_multiple",
                    "risk_level": "high",
                    "workbook": "generic_source.xlsx",
                    "sheet": "Assay Alpha",
                    "columns": ["value"],
                    "row_offset": 12,
                    "support_rows": 12,
                    "overlap_rows": 12,
                    "support_rate": 1.0,
                },
                {
                    "finding_id": "GEN-RATIO-001",
                    "category": "long_format_paired_ratio_reuse",
                    "risk_level": "high",
                    "workbook": "generic_source.xlsx",
                    "sheet": "Assay Beta",
                    "columns": ["group_a", "group_b"],
                    "pair_id_offset": 6,
                    "matched_pair_groups": 6,
                },
            ],
        },
    )
    write_json(
        tmp_path / "agent_claim_extractor.json",
        {
            "claims": [
                {
                    "claim_id": "AC-GENERIC-001",
                    "claim_text": "The paired assay differs between two study groups.",
                    "evidence_refs": ["source_data_pair_forensics:GEN-ROW-001"],
                }
            ]
        },
    )
    write_json(
        tmp_path / "agent_source_data_auditor.json",
        {
            "claim_to_source_data": [
                {
                    "claim_id": "AC-GENERIC-001",
                    "source_data_refs": ["source_data_pair_forensics:GEN-ROW-001"],
                    "needs_human_review": True,
                }
            ],
            "manual_review_tasks": [
                {
                    "task_id": "MR-GENERIC-001",
                    "priority": "high",
                    "question": "Check whether the row offset is a valid paired export convention.",
                    "evidence_refs": ["source_data_pair_forensics:GEN-ROW-001"],
                }
            ],
        },
    )
    write_json(tmp_path / "source_data_findings.json", {"summary": {}, "priority_findings": []})
    write_json(tmp_path / "source_data_profile.json", {"summary": {"workbook_count": 1, "sheet_count": 2}})
    write_json(tmp_path / "static_audit_bundle.json", {"agent_traces": [], "claim_mappings": [1]})

    html = render_static_audit_html(tmp_path, "case-generic")

    assert "配对样本固定行偏移与比例复用" in html
    assert "GEN-ROW-001" in html
    assert "Assay Alpha" in html
    assert "generic_source.xlsx" in html
    assert "Fig.7d" not in html
    assert "ROS-0001" not in html
    assert "PT/RT" not in html


def test_static_audit_html_report_merges_source_and_pair_priority_findings(tmp_path) -> None:
    write_json(
        tmp_path / "source_data_findings.json",
        {
            "summary": {"priority_findings": 1},
            "priority_findings": [
                {
                    "finding_id": "SRC-001",
                    "category": "fixed_difference",
                    "risk_level": "medium",
                    "workbook": "source.xlsx",
                    "sheet": "Endpoint A",
                    "column_pair": ["B", "C"],
                    "support_rows": 18,
                    "overlap_rows": 18,
                }
            ],
        },
    )
    write_json(
        tmp_path / "source_data_pair_forensics.json",
        {
            "summary": {"priority_findings": 1},
            "priority_findings": [
                {
                    "finding_id": "PAIR-001",
                    "category": "row_offset_scalar_multiple",
                    "risk_level": "high",
                    "workbook": "source.xlsx",
                    "sheet": "Endpoint B",
                    "columns": ["value"],
                    "row_offset": 8,
                    "support_rows": 8,
                    "overlap_rows": 8,
                }
            ],
        },
    )
    write_json(tmp_path / "static_audit_bundle.json", {"agent_traces": [], "claim_mappings": []})

    html = render_static_audit_html(tmp_path, "case-merged")

    assert "原始高优先级 evidence cards（展示 2 / 2 条）" in html
    assert "SRC-001" in html
    assert "PAIR-001" in html


def test_static_audit_html_report_uses_pass_verdict_without_findings(tmp_path) -> None:
    write_json(
        tmp_path / "audit_run_manifest.json",
        {"steps": [{"key": "evidence_ledger", "title": "Evidence ledger", "status": "ran"}]},
    )
    write_json(
        tmp_path / "static_audit_bundle.json",
        {
            "evidence_items": [{"evidence_id": "EV-001"}],
            "claims": [],
            "findings": [],
            "claim_mappings": [],
            "agent_traces": [],
            "execution_status": {"status": "not_provided"},
        },
    )

    html = render_static_audit_html(tmp_path, "case-clean")

    assert "Needs Human Review" not in html
    assert "未见高优先级自动 finding" in html


def test_static_audit_html_report_renders_canonical_non_source_data_finding(tmp_path) -> None:
    write_json(
        tmp_path / "static_audit_bundle.json",
        {
            "evidence_items": [
                {
                    "evidence_id": "EV-IMG-001",
                    "kind": "image",
                    "source_path": "images/figure_1.png",
                    "locator": {"figure": "Fig. 1"},
                }
            ],
            "claims": [
                {
                    "claim_id": "CL-IMG-001",
                    "text": "The image panels represent independent conditions.",
                    "claim_type": "figure_trace",
                }
            ],
            "findings": [
                {
                    "finding_id": "VF-001",
                    "category": "near_duplicate_image",
                    "risk_level": "medium",
                    "summary": "Near-duplicate image candidate requires visual review.",
                    "evidence_refs": ["EV-IMG-001"],
                    "metadata": {"source_artifact": "image_relationships.json"},
                }
            ],
            "claim_mappings": [
                {
                    "mapping_id": "CM-IMG-001",
                    "claim_id": "CL-IMG-001",
                    "evidence_refs": ["EV-IMG-001"],
                    "confidence": "medium",
                    "finding_refs": ["VF-001"],
                }
            ],
            "agent_traces": [],
            "execution_status": {"status": "not_provided"},
        },
    )

    html = render_static_audit_html(tmp_path, "case-visual")

    assert "VF-001" in html
    assert "near_duplicate_image" in html
    assert "Evidence refs" in html


def test_static_audit_html_report_renders_paperfraud_rule_matches(tmp_path) -> None:
    write_json(
        tmp_path / "paperfraud_rule_matches.json",
        {
            "summary": {
                "total_rules_loaded": 48,
                "total_triggered": 1,
                "methodology_review_triggered": 1,
                "fraud_detection_triggered": 0,
                "red_count": 0,
                "orange_count": 1,
                "yellow_count": 0,
            },
            "triggered_rules": [
                {
                    "rule_id": "statistical_methods.effect_size_missing",
                    "title": "Only p-value reported without effect size",
                    "severity": "orange",
                    "rule_type": "methodology_review",
                    "category": "统计方法审查",
                    "evidence": "Matched p-value language in Methods.",
                    "human_review": "Check whether the manuscript reports effect size and uncertainty.",
                }
            ],
            "reviewer_form": [
                {
                    "rule_id": "statistical_methods.effect_size_missing",
                    "human_review_guide": "Check whether the manuscript reports effect size and uncertainty.",
                }
            ],
        },
    )
    write_json(tmp_path / "static_audit_bundle.json", {"agent_traces": [], "claim_mappings": []})

    html = render_static_audit_html(tmp_path, "case-paperfraud")

    assert "PaperFraud 规则库命中" in html
    assert "statistical_methods.effect_size_missing" in html
    assert "orange" in html
    assert "Check whether the manuscript reports effect size" in html
