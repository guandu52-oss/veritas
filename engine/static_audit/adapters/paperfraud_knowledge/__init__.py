"""PaperFraud Knowledge Base Adapter for Veritas.

Loads structured YAML detection rules and provides rule matching
against paper text. Register as a Veritas Tool via engine/tools/registry.py.

Rules are split into two types:
  - methodology_review: Study design quality, confounding, reporting standards
  - fraud_detection: Numerical forensics, text patterns, statistical anomalies

Usage::

    from engine.static_audit.adapters.paperfraud_knowledge import (
        load_knowledge_base,
        match_rules,
    )

    rules = load_knowledge_base()
    matches = match_rules(rules, paper_full_text=full_text)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PaperFraudRule:
    """A single detection rule from the PaperFraud knowledge base."""

    id: str
    category: str
    subcategory: str = ""
    title: str = ""
    severity: str = "yellow"  # red / orange / yellow
    rule_type: str = "methodology_review"  # methodology_review | fraud_detection
    description: str = ""
    detection: dict = field(default_factory=dict)
    evidence_template: str = ""
    human_review: str = ""
    references: list[str] = field(default_factory=list)
    source: str = ""


@dataclass
class RuleMatch:
    """Result of matching a rule against paper text."""

    rule: PaperFraudRule
    triggered: bool = False
    excerpts: list[str] = field(default_factory=list)
    evidence: str = ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_RULES_DIR = Path(__file__).resolve().parent / "rules"
_REPORTING_DIR = Path(__file__).resolve().parent / "reporting_standards"


def load_knowledge_base() -> list[PaperFraudRule]:
    """Load all detection rules from YAML files.

    Returns:
        Flat list of all rules across all categories.
    """
    if not _RULES_DIR.is_dir():
        return []

    rules: list[PaperFraudRule] = []
    for yaml_file in sorted(_RULES_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            file_type = raw.get("type", "methodology_review")
            for rd in raw.get("rules", []):
                rules.append(PaperFraudRule(
                    id=rd.get("id", f"{yaml_file.stem}.unknown"),
                    category=rd.get("category", raw.get("category", "")),
                    subcategory=rd.get("subcategory", ""),
                    title=rd.get("title", ""),
                    severity=rd.get("severity", "yellow"),
                    rule_type=rd.get("type", file_type),
                    description=rd.get("description", ""),
                    detection=rd.get("detection", {}),
                    evidence_template=rd.get("evidence_template", ""),
                    human_review=rd.get("human_review", ""),
                    references=rd.get("references", []),
                    source=rd.get("source", f"paperfraud/{yaml_file.name}"),
                ))
        except Exception as e:
            print(f"[PaperFraud KB] Warning: failed to load {yaml_file.name}: {e}")

    return rules


def load_reporting_standards() -> dict[str, list[dict]]:
    """Load reporting standards checklists (CONSORT, etc.)."""
    standards: dict[str, list[dict]] = {}
    if not _REPORTING_DIR.is_dir():
        return standards

    for yaml_file in sorted(_REPORTING_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            standards[yaml_file.stem] = raw.get("checklist", [])
        except Exception:
            pass

    return standards


# ---------------------------------------------------------------------------
# Rule matching engine
# ---------------------------------------------------------------------------


def match_rules(
    rules: list[PaperFraudRule],
    *,
    paper_full_text: str = "",
    paper_methods: str = "",
    paper_abstract: str = "",
) -> list[RuleMatch]:
    """Match rules against paper text and return triggered results.

    Args:
        rules: Rules loaded from load_knowledge_base().
        paper_full_text: Full paper markdown/text.
        paper_methods: Methods section text.
        paper_abstract: Abstract text.

    Returns:
        One RuleMatch per rule. Check `.triggered` for active rules.
    """
    all_text = paper_full_text or paper_methods or paper_abstract
    results = []

    for rule in rules:
        match = _match_single(rule, all_text, paper_methods)
        results.append(match)

    return results


def _match_single(
    rule: PaperFraudRule, all_text: str, methods_text: str
) -> RuleMatch:
    """Check a single rule against paper text."""
    triggers = rule.detection.get("triggers", {})
    # negative_triggers may be inside detection.triggers or directly under detection
    negatives = triggers.get("negative_triggers", []) or rule.detection.get("negative_triggers", [])
    keywords = triggers.get("keywords", [])
    study_types = triggers.get("study_types", [])

    if not all_text:
        return RuleMatch(rule=rule, triggered=False)

    # ── Study type gate ────────────────────────────────────────────
    if study_types:
        type_matched = False
        for st in study_types:
            if _check_study_type(st, methods_text or all_text):
                type_matched = True
                break
        if not type_matched:
            return RuleMatch(rule=rule, triggered=False)

    # ── Keyword triggers ───────────────────────────────────────────
    excerpts: list[str] = []
    if keywords:
        any_match = False
        for kw in keywords:
            if isinstance(kw, str):
                excerpt = _search_pattern(kw, "keyword", all_text)
            else:
                excerpt = _search_pattern(
                    kw.get("pattern", ""),
                    kw.get("type", "keyword"),
                    all_text,
                )
            if excerpt:
                any_match = True
                excerpts.append(excerpt)
        if not any_match:
            return RuleMatch(rule=rule, triggered=False)

    # ── Negative triggers ──────────────────────────────────────────
    for neg in negatives:
        if _search_pattern(neg, "keyword", all_text):
            return RuleMatch(rule=rule, triggered=False)

    # ── Build evidence ─────────────────────────────────────────────
    excerpt = excerpts[0][:300] if excerpts else "N/A"
    evidence = rule.evidence_template.format(excerpt=excerpt)

    return RuleMatch(
        rule=rule,
        triggered=True,
        excerpts=excerpts,
        evidence=evidence,
    )


def _search_pattern(
    pattern: str, pattern_type: str, text: str
) -> str | None:
    """Search for a keyword or regex pattern in text."""
    if not pattern or not text:
        return None

    flags = re.IGNORECASE

    if pattern_type == "regex":
        try:
            m = re.search(pattern, text, flags)
        except re.error:
            return None
    else:
        m = re.search(re.escape(pattern), text, flags)

    if m:
        start = max(0, m.start() - 60)
        end = min(len(text), m.end() + 60)
        return text[start:end].replace("\n", " ").strip()
    return None


def _check_study_type(study_type: str, text: str) -> bool:
    """Check if a study type is mentioned in text."""
    patterns = {
        "cross_sectional": r"\b(?:cross[ .-]sectional|prevalence\s+survey)\b",
        "case_control": r"\b(?:case[ .-]control|case control)\b",
        "cohort": r"\b(?:cohort|prospective\s+(?:cohort|study|follow)|longitudinal)\b",
        "rct": r"\b(?:randomi[sz]ed\s+(?:controlled\s+)?(?:trial|study|experiment)|RCT\b|clinical\s+trial)\b",
        "ecological": r"\b(?:ecological\s+study|ecological\s+analysis)\b",
        "systematic_review": r"\b(?:systematic\s+review|meta[ .-]analysis)\b",
        "observational": r"\b(?:observational|cohort|case[ .-]control|cross[ .-]sectional)\b",
        "diagnostic": r"\b(?:diagnostic\s+(?:accuracy|test|performance)|ROC\s+curve|AUC\b)\b",
        "experimental": r"\b(?:HEK293|HeLa|cell\s+line|cell\s+culture|primary\s+cells?|mice|mouse|rats?|in\s+vivo|in\s+vitro|animal\s+model)\b",
    }
    pat = patterns.get(study_type)
    if pat:
        return bool(re.search(pat, text, re.IGNORECASE))
    return bool(re.search(re.escape(study_type), text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def summarize_matches(matches: list[RuleMatch]) -> dict:
    """Summarize triggered rules by severity and type.

    Returns a dict suitable for inclusion in Veritas static_audit_bundle.
    """
    triggered = [m for m in matches if m.triggered]

    by_severity: dict[str, list[dict]] = defaultdict(list)
    by_type: dict[str, int] = defaultdict(int)

    for m in triggered:
        by_severity[m.rule.severity].append({
            "rule_id": m.rule.id,
            "title": m.rule.title,
            "category": m.rule.category,
            "evidence": m.evidence,
            "human_review": m.rule.human_review,
        })
        by_type[m.rule.rule_type] = by_type.get(m.rule.rule_type, 0) + 1

    return {
        "total_rules_loaded": len(matches),
        "total_triggered": len(triggered),
        "methodology_review_triggered": by_type.get("methodology_review", 0),
        "fraud_detection_triggered": by_type.get("fraud_detection", 0),
        "red_count": len(by_severity.get("red", [])),
        "orange_count": len(by_severity.get("orange", [])),
        "yellow_count": len(by_severity.get("yellow", [])),
        "by_severity": dict(by_severity),
    }


def generate_reviewer_form(
    rules: list[PaperFraudRule],
) -> list[dict]:
    """Generate a reviewer scoring form from loaded rules.

    Each row = one rule with Y/N/Partial scoring and comment fields.
    """
    form = []
    for rule in rules:
        form.append({
            "rule_id": rule.id,
            "category": rule.category,
            "title": rule.title,
            "severity": rule.severity,
            "rule_type": rule.rule_type,
            "human_review_guide": rule.human_review,
            "score": "",            # Y / N / Partial
            "evidence_found": "",
            "reviewer_comment": "",
        })
    return form
