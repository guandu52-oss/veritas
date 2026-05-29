from __future__ import annotations

from engine.reporting.models import VerificationReport


def render_markdown(report: VerificationReport) -> str:
    lines: list[str] = []
    lines.append(f"# Verification Report: {report.project_name}")
    lines.append("")
    lines.append(f"- Report ID: `{report.report_id}`")
    lines.append(f"- Generated At: `{report.generated_at}`")
    lines.append(f"- Verification Level: `{report.verification_level}`")
    lines.append(f"- Overall Status: `{report.overall_status}`")
    lines.append(f"- Role View: `{report.role}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Findings: `{report.summary['total_findings']}`")
    lines.append(f"- Critical: `{report.summary['critical']}`")
    lines.append(f"- Warning: `{report.summary['warning']}`")
    lines.append(f"- Claims Checked: `{report.summary['claims_checked']}`")
    lines.append("")
    lines.append("## Verification Scope")
    lines.append("")
    for key, value in report.artifacts.items():
        if isinstance(value, list):
            joined = ", ".join(item for item in value if item) or "N/A"
            lines.append(f"- {key}: `{joined}`")
        else:
            lines.append(f"- {key}: `{value or 'N/A'}`")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    for check in report.checks:
        lines.append(f"- {check.title}: `{check.status}`")
        lines.append(f"  - {check.detail}")
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    if not report.findings:
        lines.append("- No findings.")
    else:
        for item in report.findings:
            lines.append(f"### {item.id} · {item.title}")
            lines.append("")
            lines.append(f"- Severity: `{item.severity}`")
            lines.append(f"- Category: `{item.category}`")
            lines.append(f"- Fact: {item.fact}")
            lines.append(f"- Inference: {item.inference}")
            lines.append(f"- Suggestion: {item.suggestion}")
            lines.append(f"- Source: `{item.source}`")
            lines.append("")
    lines.append("## Claim Table")
    lines.append("")
    if not report.claim_table:
        lines.append("- No structured claims were checked.")
    else:
        lines.append("| ID | Source | Dataset | Metric | Expected | Actual | Status |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in report.claim_table:
            lines.append(
                f"| {row['id']} | {row['source']} | {row['dataset']} | {row['metric']} | "
                f"{row['expected']} | {row.get('actual') or 'N/A'} | {row['status']} |"
            )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for item in report.limitations or ["No additional limitations recorded."]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    for item in report.notes:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
