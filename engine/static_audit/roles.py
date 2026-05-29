from __future__ import annotations

from dataclasses import dataclass, field

from engine.static_audit.models import AgentTrace


@dataclass(frozen=True)
class RoleDefinition:
    role_id: str
    title: str
    real_in_v1: bool
    input_artifacts: tuple[str, ...]
    output_artifact: str
    output_contract: dict[str, str] = field(default_factory=dict)


ROLE_DEFINITIONS: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        role_id="claim_extractor",
        title="ClaimExtractor",
        real_in_v1=True,
        input_artifacts=("full.md", "evidence_ledger.json"),
        output_artifact="agent_claim_extractor.json",
        output_contract={
            "claims": "List of numeric, method, figure_trace, code_execution, or material_completeness claims.",
        },
    ),
    RoleDefinition(
        role_id="source_data_auditor",
        title="SourceDataAuditor",
        real_in_v1=True,
        input_artifacts=(
            "artifact_summary.source_data_profile",
            "source_data_findings.json",
            "agent_claim_extractor.json",
        ),
        output_artifact="agent_source_data_auditor.json",
        output_contract={
            "finding_reviews": "Review deterministic Source Data findings with benign explanations.",
            "claim_mappings": "Candidate mappings between paper claims and Source Data evidence.",
        },
    ),
    RoleDefinition(
        role_id="visual_triage",
        title="VisualTriageAgent",
        real_in_v1=False,
        input_artifacts=("images/", "exact_image_duplicates.json", "image_similarity_candidates.json"),
        output_artifact="agent_visual_triage.json",
    ),
    RoleDefinition(
        role_id="digit_pattern",
        title="DigitPatternAgent",
        real_in_v1=False,
        input_artifacts=("numeric_forensics.json", "source_data_profile.json"),
        output_artifact="agent_digit_pattern.json",
    ),
    RoleDefinition(
        role_id="math_consistency",
        title="MathConsistencyAgent",
        real_in_v1=False,
        input_artifacts=("source_data_findings.json", "numeric_forensics.json"),
        output_artifact="agent_math_consistency.json",
    ),
    RoleDefinition(
        role_id="domain_sanity",
        title="DomainSanityAgent",
        real_in_v1=False,
        input_artifacts=("full.md", "source_data_findings.json"),
        output_artifact="agent_domain_sanity.json",
    ),
    RoleDefinition(
        role_id="defense",
        title="DefenseAgent",
        real_in_v1=False,
        input_artifacts=("source_data_findings.json", "agent_source_data_auditor.json"),
        output_artifact="agent_defense.json",
    ),
    RoleDefinition(
        role_id="judge",
        title="JudgeAgent",
        real_in_v1=True,
        input_artifacts=(
            "agent_claim_extractor.json",
            "agent_source_data_auditor.json",
            "source_data_findings.json",
            "numeric_forensics.json",
        ),
        output_artifact="agent_judge.json",
        output_contract={
            "summary": "Report-ready synthesis without final misconduct judgment.",
            "risk_suggestions": "Risk suggestions constrained by deterministic findings.",
        },
    ),
)


def role_catalog() -> list[dict[str, object]]:
    return [
        {
            "role_id": role.role_id,
            "title": role.title,
            "real_in_v1": role.real_in_v1,
            "input_artifacts": list(role.input_artifacts),
            "output_artifact": role.output_artifact,
            "output_contract": role.output_contract,
        }
        for role in ROLE_DEFINITIONS
    ]


def skipped_trace(role: RoleDefinition, detail: str = "Role schema reserved for static_audit_protocol.v1.") -> AgentTrace:
    return AgentTrace(
        role_id=role.role_id,
        status="skipped",
        input_artifacts=list(role.input_artifacts),
        output_path=role.output_artifact,
        output_summary={"real_in_v1": role.real_in_v1},
        detail=detail,
    )
