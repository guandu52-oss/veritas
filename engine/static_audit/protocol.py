from __future__ import annotations

from dataclasses import dataclass

from engine.static_audit.models import STATIC_AUDIT_PROTOCOL_VERSION


@dataclass(frozen=True)
class StaticAuditStage:
    stage_id: str
    title: str
    kind: str
    required: bool = True


STATIC_AUDIT_STAGES: tuple[StaticAuditStage, ...] = (
    StaticAuditStage("discover_inputs", "Discover paper inputs", "deterministic"),
    StaticAuditStage("agent_claim_extraction", "ClaimExtractor role", "agent"),
    StaticAuditStage("mineru_parse", "MinerU PDF parse", "deterministic"),
    StaticAuditStage("evidence_ledger", "Build evidence ledger", "deterministic"),
    StaticAuditStage("numeric_forensics", "PDF numeric forensics", "deterministic"),
    StaticAuditStage("source_data_profile", "Source Data profile", "deterministic"),
    StaticAuditStage("source_data_findings", "Source Data findings", "deterministic"),
    StaticAuditStage("image_exact_duplicates", "Exact image duplicate check", "deterministic"),
    StaticAuditStage("image_similarity_candidates", "Near-duplicate image candidates", "deterministic", required=False),
    StaticAuditStage("agent_source_data_audit", "SourceDataAuditor role", "agent"),
    StaticAuditStage("agent_supporting_roles", "Supporting static-audit roles", "agent", required=False),
    StaticAuditStage("agent_judge", "JudgeAgent role", "agent"),
    StaticAuditStage("bundle", "Write static audit bundle", "deterministic"),
    StaticAuditStage("legacy_report", "Render compatible report and manifest", "deterministic"),
)


def protocol_manifest() -> dict[str, object]:
    return {
        "protocol_version": STATIC_AUDIT_PROTOCOL_VERSION,
        "stages": [
            {
                "stage_id": stage.stage_id,
                "title": stage.title,
                "kind": stage.kind,
                "required": stage.required,
            }
            for stage in STATIC_AUDIT_STAGES
        ],
    }

