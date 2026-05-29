from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    category: str
    status: str
    fact: str
    inference: str
    suggestion: str
    source: str
    rerun_required: bool = False


@dataclass
class CheckStep:
    key: str
    title: str
    status: str
    detail: str


@dataclass
class VerificationReport:
    report_id: str
    generated_at: str
    project_name: str
    verification_level: str
    overall_status: str
    role: str
    summary: dict[str, Any]
    artifacts: dict[str, Any]
    checks: list[CheckStep] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    claim_table: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "project_name": self.project_name,
            "verification_level": self.verification_level,
            "overall_status": self.overall_status,
            "role": self.role,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "checks": [asdict(item) for item in self.checks],
            "findings": [asdict(item) for item in self.findings],
            "limitations": self.limitations,
            "claim_table": self.claim_table,
            "notes": self.notes,
        }


def report_from_dict(data: dict[str, Any]) -> VerificationReport:
    return VerificationReport(
        report_id=data["report_id"],
        generated_at=data["generated_at"],
        project_name=data["project_name"],
        verification_level=data["verification_level"],
        overall_status=data["overall_status"],
        role=data["role"],
        summary=data["summary"],
        artifacts=data["artifacts"],
        checks=[CheckStep(**item) for item in data.get("checks", [])],
        findings=[Finding(**item) for item in data.get("findings", [])],
        limitations=data.get("limitations", []),
        claim_table=data.get("claim_table", []),
        notes=data.get("notes", []),
    )
