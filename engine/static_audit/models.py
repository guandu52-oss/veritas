from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

STATIC_AUDIT_BUNDLE_SCHEMA_VERSION = "1.0"
STATIC_AUDIT_PROTOCOL_VERSION = "static_audit_protocol.v1"

EvidenceKind = Literal[
    "page",
    "table",
    "image",
    "sheet",
    "cell",
    "command",
    "output_artifact",
]

RiskLevel = Literal["info", "low", "medium", "high", "critical"]
Status = Literal[
    "pending",
    "ran",
    "reused",
    "skipped",
    "warning",
    "failed",
    "not_run",
    "not_provided",
    "missing_material",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class EvidenceItem:
    evidence_id: str
    kind: EvidenceKind
    source_path: str
    locator: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Claim:
    claim_id: str
    text: str
    claim_type: str
    source: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    status: Status = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    finding_id: str
    category: str
    risk_level: RiskLevel
    summary: str
    evidence_refs: list[str] = field(default_factory=list)
    claim_refs: list[str] = field(default_factory=list)
    benign_explanations: list[str] = field(default_factory=list)
    pressure_test_result: str = ""
    manual_review_note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaimMapping:
    mapping_id: str
    claim_id: str
    evidence_refs: list[str]
    confidence: str
    status: str = "candidate_mapping"
    finding_refs: list[str] = field(default_factory=list)
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolRun:
    tool_id: str
    step_key: str
    status: Status
    title: str = ""
    command: list[str] | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    runtime_seconds: float | None = None
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTrace:
    role_id: str
    status: Status
    input_artifacts: list[str] = field(default_factory=list)
    output_path: str | None = None
    output_summary: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    detail: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionStatus:
    status: Status = "not_provided"
    runtime_backend: str | None = None
    manifest_path: str | None = None
    summary: str = "Code execution audit is not connected for this static-audit run."
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StaticAuditBundle:
    case_id: str
    inputs: dict[str, Any]
    schema_version: str = STATIC_AUDIT_BUNDLE_SCHEMA_VERSION
    protocol_version: str = STATIC_AUDIT_PROTOCOL_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    tool_runs: list[ToolRun] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    claim_mappings: list[ClaimMapping] = field(default_factory=list)
    agent_traces: list[AgentTrace] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    execution_status: ExecutionStatus = field(default_factory=ExecutionStatus)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")


def load_static_audit_bundle(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

