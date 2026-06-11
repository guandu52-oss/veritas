"""Run PaperFraud knowledge-base rules inside the Veritas static audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.static_audit.adapters.paperfraud_knowledge import (
    RuleMatch,
    generate_reviewer_form,
    load_knowledge_base,
    match_rules,
    summarize_matches,
)
from engine.static_audit.models import Finding


SEVERITY_TO_RISK = {
    "red": "high",
    "orange": "medium",
    "yellow": "low",
    "green": "info",
}


def run_paperfraud_rule_match(full_md_path: Path, output_path: Path) -> dict[str, Any]:
    """Match PaperFraud rules against parsed paper text and write JSON artifact."""
    paper_text = full_md_path.read_text(encoding="utf-8", errors="ignore") if full_md_path.exists() else ""
    rules = load_knowledge_base()
    matches = match_rules(rules, paper_full_text=paper_text, paper_methods=paper_text)
    artifact = {
        "schema_version": "1.0",
        "tool_id": "paperfraud.rule_match",
        "source": "engine/static_audit/adapters/paperfraud_knowledge",
        "input_artifacts": [str(full_md_path)],
        "summary": summarize_matches(matches),
        "triggered_rules": [_match_to_dict(match) for match in matches if match.triggered],
        "reviewer_form": generate_reviewer_form(rules),
        "limitations": [
            "Keyword/regex rule matches are review prompts, not final misconduct findings.",
            "Negative matches can miss method descriptions that use unusual wording.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def paperfraud_findings_from_matches(artifact: dict[str, Any]) -> list[Finding]:
    """Convert triggered PaperFraud rules into canonical static-audit findings."""
    findings: list[Finding] = []
    for index, item in enumerate(artifact.get("triggered_rules") or [], start=1):
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or f"rule-{index}")
        severity = str(item.get("severity") or "yellow")
        findings.append(
            Finding(
                finding_id=f"PF-{_safe_id(rule_id)}",
                category=f"paperfraud.{item.get('rule_type') or 'methodology_review'}",
                risk_level=SEVERITY_TO_RISK.get(severity, "low"),
                summary=str(item.get("title") or rule_id),
                evidence_refs=[],
                benign_explanations=[
                    "Rule match may reflect legitimate reporting language.",
                    "Reviewer should verify whether the method was actually absent or only phrased differently.",
                ],
                pressure_test_result="requires_human_review",
                manual_review_note=str(item.get("human_review") or ""),
                metadata={
                    "source_artifact": "paperfraud_rule_matches.json",
                    "rule_id": rule_id,
                    "severity": severity,
                    "category": item.get("category") or "",
                    "evidence": item.get("evidence") or "",
                    "excerpts": item.get("excerpts") or [],
                },
            )
        )
    return findings


def _match_to_dict(match: RuleMatch) -> dict[str, Any]:
    rule = match.rule
    return {
        "rule_id": rule.id,
        "category": rule.category,
        "subcategory": rule.subcategory,
        "title": rule.title,
        "severity": rule.severity,
        "rule_type": rule.rule_type,
        "evidence": match.evidence,
        "excerpts": match.excerpts,
        "human_review": rule.human_review,
        "references": rule.references,
        "source": rule.source,
    }


def _safe_id(value: str) -> str:
    cleaned = "".join(ch.upper() if ch.isalnum() else "-" for ch in value)
    return "-".join(part for part in cleaned.split("-") if part) or "RULE"
