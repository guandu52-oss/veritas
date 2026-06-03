#!/usr/bin/env python3
"""Run the first-party Veritas static paper-audit pipeline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.static_audit.models import (  # noqa: E402
    AgentTrace,
    Claim,
    ClaimMapping,
    EvidenceItem,
    ExecutionStatus,
    Finding,
    Status,
    StaticAuditBundle,
    ToolRun,
)
from engine.static_audit.html_report import write_static_audit_html  # noqa: E402
from engine.static_audit.investigation import (  # noqa: E402
    InvestigationAction,
    InvestigationRecord,
    append_investigation_record,
    normalize_expected_evidence_type,
    read_investigation_records,
)
from engine.static_audit.materials import (  # noqa: E402
    build_material_inventory,
    fallback_optional_lanes,
    write_material_inventory,
)
from engine.static_audit.tools.paperfraud_rules import (  # noqa: E402
    paperfraud_findings_from_matches,
    run_paperfraud_rule_match,
)
from engine.static_audit.roles import ROLE_DEFINITIONS, RoleDefinition, skipped_trace  # noqa: E402
from engine.investigation.opencode_agent import (  # noqa: E402
    DEFAULT_SOURCE_FINDING_PARAMS,
    AgentRunResult,
    result_metadata,
    run_agent_investigation_plan,
    run_agent_material_plan,
    run_agent_plan,
    run_agent_review,
    run_agent_role,
    write_agent_result,
)
from engine.tools.registry import (  # noqa: E402
    IMAGE_SIMILARITY_TOOL_ID,
    PAPER_STATIC_AUDIT_TOOL_IDS,
    PAPERFRAUD_RULE_MATCH_TOOL_ID,
    SOURCE_DATA_FINDINGS_TOOL_ID,
    SOURCE_DATA_PAIR_FORENSICS_TOOL_ID,
    STATIC_AUDIT_V1_TOOL_IDS,
    selected_tool_ids_from_plan,
    source_data_findings_params_from_plan,
)


AUDITOR_ROOT = PROJECT_ROOT / "third_party" / "research-integrity-auditor"
MAX_INVESTIGATION_ROUNDS = 3

STEP_TOOL_IDS = {
    "mineru": "mineru.parse_pdf",
    "evidence_ledger": "paper.evidence_ledger",
    "numeric_forensics": "paper.numeric_forensics",
    "paperfraud_rule_match": PAPERFRAUD_RULE_MATCH_TOOL_ID,
    "material_inventory": "material.inventory",
    "agent_material_plan": "agent.material_plan",
    "source_data_profile": "source_data.profile",
    "source_data_findings": "source_data.findings",
    "source_data_pair_forensics": "source_data.pair_forensics",
    "exact_image_duplicates": "image.exact_duplicates",
    "image_similarity_candidates": "image.similarity_candidates",
    "agent_plan": "agent.plan",
    "agent_review": "agent.review",
    "agent_role_claim_extractor": "agent.role.claim_extractor",
    "agent_role_source_data_auditor": "agent.role.source_data_auditor",
    "agent_role_judge": "agent.role.judge",
    "static_audit_bundle": "static_audit.bundle",
    "report": "report.render_markdown",
    "html_report": "report.render_static_html",
}


@dataclass
class StepResult:
    key: str
    title: str
    status: str
    detail: str
    command: list[str] | None = None


ProgressCallback = Callable[[dict[str, Any]], None]


def emit_progress(progress: ProgressCallback | None, event: str, **payload: Any) -> None:
    if progress is None:
        return
    progress(
        {
            "event": event,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **payload,
        }
    )


def command_preview(command: list[str] | None) -> str | None:
    if not command:
        return None
    if len(command) >= 2 and Path(command[0]).name == "opencode" and command[1] == "run":
        model = None
        if "--model" in command:
            index = command.index("--model")
            if index + 1 < len(command):
                model = command[index + 1]
        return f"opencode run --format json --model {model or '<unknown>'} ..."
    preview = [Path(part).name if index == 0 else part for index, part in enumerate(command[:6])]
    suffix = " ..." if len(command) > 6 else ""
    return " ".join(preview) + suffix


def emit_step_start(
    progress: ProgressCallback | None,
    key: str,
    title: str,
    detail: str = "",
    command: list[str] | None = None,
) -> None:
    emit_progress(
        progress,
        "step_start",
        key=key,
        title=title,
        status="running",
        detail=detail,
        command_preview=command_preview(command),
    )


def emit_step_result(progress: ProgressCallback | None, step: StepResult) -> None:
    emit_progress(
        progress,
        "step_result",
        key=step.key,
        title=step.title,
        status=step.status,
        detail=step.detail,
        command_preview=command_preview(step.command),
    )


def record_step(
    steps: list[StepResult],
    step: StepResult,
    progress: ProgressCallback | None,
) -> StepResult:
    steps.append(step)
    emit_step_result(progress, step)
    return step


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Veritas paper audit from a local paper directory."
    )
    parser.add_argument("paper_dir", help="Directory containing paper PDF and optional Source Data.")
    parser.add_argument("--case-id", help="Case id used under outputs/<case-id>.")
    parser.add_argument("--output-root", default="outputs", help="Output root directory.")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Remove the case audit workdir before running; guarantees previous MinerU outputs are not reused.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run steps even if expected outputs already exist.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not load local .env into subprocess environment.",
    )
    parser.add_argument(
        "--agent-mode",
        choices=["off", "plan", "review", "full"],
        default="full",
        help="opencode Agent mode: off disables Agent, plan only tunes deterministic steps, review only interprets artifacts, full does both.",
    )
    parser.add_argument(
        "--agent-model",
        default="dashscope/qwen3.7-max",
        help="opencode model id used for Agent plan/review.",
    )
    parser.add_argument(
        "--opencode-bin",
        default="opencode",
        help="opencode executable path.",
    )
    parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=300,
        help="Timeout for each opencode Agent call.",
    )
    parser.add_argument(
        "--agent-max-retries",
        type=int,
        default=1,
        help="Retries after invalid Agent JSON output.",
    )
    return parser.parse_args()


def safe_remove_workdir(workdir: Path, output_root: Path) -> None:
    if not workdir.exists():
        return
    resolved_workdir = workdir.resolve()
    resolved_output_root = output_root.resolve()
    if resolved_workdir == resolved_output_root:
        raise ValueError(f"Refusing to remove output root: {resolved_workdir}")
    if resolved_workdir.name != "research-integrity-audit":
        raise ValueError(f"Refusing to remove unexpected workdir: {resolved_workdir}")
    if not resolved_workdir.is_relative_to(resolved_output_root):
        raise ValueError(f"Refusing to remove path outside output root: {resolved_workdir}")
    shutil.rmtree(resolved_workdir)


def load_env(include_env_file: bool) -> dict[str, str]:
    env = os.environ.copy()
    if not include_env_file:
        return env
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return env
    for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in env:
            env[key] = value
    return env


def discover_pdf(paper_dir: Path) -> Path:
    pdfs = sorted(path for path in paper_dir.glob("*.pdf") if path.is_file())
    if not pdfs:
        raise FileNotFoundError(f"No PDF found in {paper_dir}")
    if len(pdfs) > 1:
        # Deterministic choice for the MVP; future manifest should remove ambiguity.
        return pdfs[0]
    return pdfs[0]


def exists_all(paths: list[Path]) -> bool:
    return all(path.exists() for path in paths)


def text_tail(value: str, limit: int = 1000) -> str:
    value = value.strip()
    if not value:
        return ""
    return value[-limit:]


def run_command(
    key: str,
    title: str,
    command: list[str],
    expected_outputs: list[Path],
    *,
    cwd: Path,
    env: dict[str, str],
    force: bool,
    attempts: int = 1,
    retry_delay_seconds: float = 0.0,
    progress: ProgressCallback | None = None,
    stream_output: bool = False,
) -> StepResult:
    if expected_outputs and exists_all(expected_outputs) and not force:
        result = StepResult(
            key=key,
            title=title,
            status="reused",
            detail="Expected outputs already exist.",
            command=command,
        )
        emit_step_result(progress, result)
        return result

    last_detail = ""
    attempts = max(1, attempts)
    emit_step_start(progress, key, title, "Running deterministic command.", command)
    for attempt in range(1, attempts + 1):
        emit_progress(
            progress,
            "step_attempt",
            key=key,
            title=title,
            attempt=attempt,
            attempts=attempts,
            command_preview=command_preview(command),
        )
        if stream_output and progress is not None:
            completed = run_command_streaming(
                key=key,
                title=title,
                command=command,
                cwd=cwd,
                env=env,
                progress=progress,
            )
        else:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode != 0:
            last_detail = f"attempt={attempt}/{attempts} exit_code={completed.returncode}"
            if completed.stderr:
                last_detail += f" stderr_tail={completed.stderr[-1000:]!r}"
            stdout_tail = text_tail(completed.stdout)
            if stdout_tail:
                last_detail += f" stdout_tail={stdout_tail!r}"
            if attempt < attempts and retry_delay_seconds > 0:
                delay = retry_delay_seconds * attempt
                emit_progress(
                    progress,
                    "command_output",
                    key=key,
                    title=title,
                    line=f"retrying after {delay:.0f}s because previous attempt failed",
                    command_preview=command_preview(command),
                )
                time.sleep(delay)
            continue
        if expected_outputs and not exists_all(expected_outputs):
            missing = [str(path) for path in expected_outputs if not path.exists()]
            last_detail = f"attempt={attempt}/{attempts} command succeeded but outputs missing: {missing}"
            stdout_tail = text_tail(completed.stdout)
            if stdout_tail:
                last_detail += f" stdout_tail={stdout_tail!r}"
            if attempt < attempts and retry_delay_seconds > 0:
                delay = retry_delay_seconds * attempt
                emit_progress(
                    progress,
                    "command_output",
                    key=key,
                    title=title,
                    line=f"retrying after {delay:.0f}s because expected outputs were missing",
                    command_preview=command_preview(command),
                )
                time.sleep(delay)
            continue
        detail = "Command completed successfully."
        if attempt > 1:
            detail = f"Command completed successfully after {attempt} attempts."
        result = StepResult(key, title, "ran", detail, command)
        emit_step_result(progress, result)
        return result
    result = StepResult(key, title, "failed", last_detail, command)
    emit_step_result(progress, result)
    return result


def run_command_streaming(
    *,
    key: str,
    title: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    progress: ProgressCallback,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    output_lines: list[str] = []
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            output_lines.append(line)
            emit_progress(
                progress,
                "command_output",
                key=key,
                title=title,
                line=line[-500:],
                command_preview=command_preview(command),
            )
    return_code = process.wait()
    return subprocess.CompletedProcess(command, return_code, "\n".join(output_lines), "")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_int(value: Any) -> str:
    return "-" if value is None else str(value)


def fmt_float(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def priority_row(finding: dict[str, Any]) -> list[str]:
    relation = finding.get("relationship_value")
    if relation is not None:
        relation_text = f"{finding.get('category')}={relation}"
    else:
        relation_text = finding.get("category", "-")
    support = finding.get("support_rows") or finding.get("equal_rows")
    overlap = finding.get("overlap_rows")
    support_text = f"{support}/{overlap}" if support and overlap else fmt_int(support)
    return [
        finding.get("finding_id", "-"),
        finding.get("risk_level", "-"),
        finding.get("workbook", "-"),
        finding.get("sheet", "-"),
        ", ".join(finding.get("column_pair") or []),
        relation_text,
        support_text,
    ]


def claim_mapping_rows(mappings: list[dict[str, Any]], limit: int = 12) -> list[list[str]]:
    rows = []
    for mapping in mappings[:limit]:
        linked = mapping.get("linked_priority_findings") or []
        claim = "-"
        claims = mapping.get("candidate_claims") or []
        if claims:
            claim = claims[0].get("text", "-")[:160]
        rows.append(
            [
                mapping.get("mapping_id", "-"),
                mapping.get("source_figure_id", "-"),
                mapping.get("workbook", "-"),
                mapping.get("sheet", "-"),
                mapping.get("review_priority", "-"),
                ", ".join(item.get("finding_id", "-") for item in linked) or "-",
                claim,
            ]
        )
    return rows


def pair_forensics_rows(findings: list[dict[str, Any]], limit: int = 12) -> list[list[str]]:
    rows = []
    for finding in findings[:limit]:
        support = (
            finding.get("support_rows")
            or finding.get("matched_pairs")
            or finding.get("matched_pair_groups")
            or finding.get("duplicate_row_count")
            or finding.get("exact_reuse_pairs")
            or "-"
        )
        overlap = finding.get("overlap_rows") or finding.get("overlap_pairs") or finding.get("overlap_pair_groups") or "-"
        columns = finding.get("columns") or finding.get("column_pair") or finding.get("column") or []
        if isinstance(columns, list):
            columns_text = ", ".join(str(item) for item in columns)
        else:
            columns_text = str(columns)
        rows.append(
            [
                finding.get("finding_id", "-"),
                finding.get("risk_level", "-"),
                finding.get("category", "-"),
                finding.get("workbook", "-"),
                finding.get("sheet", "-"),
                finding.get("row_offset") or finding.get("pair_id_offset") or "-",
                columns_text or "-",
                f"{support}/{overlap}",
            ]
        )
    return rows


def canonical_claim_mapping_rows(
    claims: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
    limit: int = 12,
) -> list[list[str]]:
    claim_by_id = {str(claim.get("claim_id")): claim for claim in claims if claim.get("claim_id")}
    rows = []
    for mapping in mappings[:limit]:
        claim = claim_by_id.get(str(mapping.get("claim_id"))) or {}
        metadata = mapping.get("metadata") if isinstance(mapping.get("metadata"), dict) else {}
        source_refs = metadata.get("source_data_refs") or mapping.get("evidence_refs") or []
        rows.append(
            [
                mapping.get("mapping_id", "-"),
                mapping.get("claim_id", "-"),
                str(claim.get("text", "-"))[:180],
                mapping.get("confidence", "-"),
                mapping.get("status", "-"),
                ", ".join(str(ref) for ref in source_refs[:4]) or "-",
            ]
        )
    return rows


def agent_step_status(status: str) -> str:
    return "ran" if status == "ok" else "warning"


def source_finding_params_from_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    return source_data_findings_params_from_plan(plan)


def source_finding_params_from_lane(lane: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(DEFAULT_SOURCE_FINDING_PARAMS)
    if not lane:
        return params
    lane_params = lane.get("params")
    if not isinstance(lane_params, dict):
        return params
    source_params = lane_params.get("source_data_findings")
    if isinstance(source_params, dict):
        for key in params:
            if key in source_params:
                params[key] = source_params[key]
    return params


def selected_xlsx_source_lane(lanes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for lane in lanes:
        if lane.get("lane_id") == "source_data_xlsx" and lane.get("status") == "selected" and lane.get("root"):
            return lane
    return None


def material_plan_from_inventory(
    *,
    case_id: str,
    inventory: dict[str, Any],
    status: str,
    detail: str,
) -> dict[str, Any]:
    lanes = fallback_optional_lanes(inventory)
    unsupported_material_types = {"structured_table_text", "raw_data", "archive"}
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "status": status,
        "detail": detail,
        "selected_optional_lanes": lanes,
        "missing_materials": [] if any(item.get("status") == "selected" for item in lanes) else ["source_data_xlsx"],
        "unsupported_materials": [
            {
                "path": item.get("relative_path") or item.get("path"),
                "material_type": item.get("material_type"),
                "reason": "Material type is inventoried but has no executable optional lane in static_audit_protocol.v1.",
            }
            for item in (inventory.get("files") or [])[:80]
            if item.get("material_type") in unsupported_material_types
        ],
        "agent_rationale": [
            "Deterministic fallback used material_inventory.json because Agent material planning was not available.",
            "Only registry-supported XLSX/XLSM Source Data lanes are executable in this MVP.",
        ],
    }


def optional_lanes_from_material_plan(
    material_plan: dict[str, Any] | None,
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    if material_plan and isinstance(material_plan.get("selected_optional_lanes"), list):
        return [item for item in material_plan["selected_optional_lanes"] if isinstance(item, dict)]
    return fallback_optional_lanes(inventory)


def resolve_selected_source_root(lane: dict[str, Any] | None, paper_dir: Path) -> Path | None:
    if not lane or not lane.get("root"):
        return None
    root = Path(str(lane["root"])).expanduser()
    if not root.is_absolute():
        root = paper_dir / root
    resolved = root.resolve()
    paper_root = paper_dir.resolve()
    if not resolved.is_dir():
        return None
    if not resolved.is_relative_to(paper_root):
        return None
    return resolved


def artifact_exists(workdir: Path, artifact: str) -> bool:
    cleaned = artifact.rstrip("/")
    if not cleaned:
        return False
    path = workdir / cleaned
    return path.exists()


def safe_action_dir_name(action_id: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in action_id).strip("_") or "action"


def investigation_action_from_dict(round_id: int, action: dict[str, Any]) -> InvestigationAction:
    return InvestigationAction(
        round_id=round_id,
        action_id=str(action.get("action_id") or f"IR-{round_id:02d}-A001"),
        tool_id=str(action.get("tool_id")),
        params=action.get("params") if isinstance(action.get("params"), dict) else {},
        hypothesis=str(action.get("hypothesis") or ""),
        depends_on_artifacts=[str(item) for item in (action.get("depends_on_artifacts") or [])],
        expected_evidence_type=normalize_expected_evidence_type(str(action.get("expected_evidence_type") or "")),
        stop_if_no_new_evidence=bool(action.get("stop_if_no_new_evidence", True)),
    )


def run_investigation_rounds(
    *,
    case_id: str,
    workdir: Path,
    source_data_dir: Path | None,
    agent_enabled: bool,
    agent_mode: str,
    force: bool,
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
    progress: ProgressCallback | None = None,
) -> tuple[list[StepResult], dict[str, Any]]:
    steps: list[StepResult] = []
    manifest: dict[str, Any] = {
        "enabled": agent_enabled,
        "max_rounds": MAX_INVESTIGATION_ROUNDS,
        "rounds_artifact": str(workdir / "investigation_rounds.jsonl"),
        "plans": [],
    }
    if not agent_enabled:
        step = StepResult(
            "agent_investigation",
            "opencode Agent 调查规划",
            "skipped",
            f"agent_mode={agent_mode} does not run AgentInvestigationPlanner.",
        )
        record_step(steps, step, progress)
        return steps, manifest

    seen_signatures = {
        str((record.get("metadata") or {}).get("signature"))
        for record in read_investigation_records(workdir)
        if (record.get("metadata") or {}).get("signature")
    }
    stop_reason = ""
    for round_id in range(1, MAX_INVESTIGATION_ROUNDS + 1):
        plan_path = workdir / f"agent_investigation_plan_round_{round_id:02d}.json"
        previous_records = read_investigation_records(workdir)
        emit_step_start(
            progress,
            f"agent_investigation_plan_round_{round_id:02d}",
            "opencode Agent 调查规划",
            f"Calling opencode investigation planner round {round_id}.",
        )
        plan_result = run_agent_investigation_plan(
            case_id=case_id,
            workdir=workdir,
            round_id=round_id,
            previous_records=previous_records,
            project_root=project_root,
            env=env,
            model=model,
            opencode_bin=opencode_bin,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        write_agent_result(plan_path, plan_result, "investigation_plan")
        manifest["plans"].append(result_metadata(plan_result, plan_path))
        plan_step = StepResult(
            f"agent_investigation_plan_round_{round_id:02d}",
            "opencode Agent 调查规划",
            agent_step_status(plan_result.status),
            plan_result.detail,
            plan_result.command,
        )
        record_step(steps, plan_step, progress)
        if not plan_result.data:
            append_investigation_record(
                workdir,
                InvestigationRecord(
                    round_id=round_id,
                    action_id=f"IR-{round_id:02d}-PLAN",
                    tool_id="agent.investigation_plan",
                    status="failed",
                    validation_status="failed",
                    detail=plan_result.detail,
                    metadata={"plan_artifact": str(plan_path)},
                ),
            )
            stop_reason = "planner_failed"
            break

        actions = [
            investigation_action_from_dict(round_id, action)
            for action in (plan_result.data.get("actions") or [])
            if isinstance(action, dict)
        ]
        if not actions:
            stop_reason = str(plan_result.data.get("stop_reason") or "no_more_tools")
            append_investigation_record(
                workdir,
                InvestigationRecord(
                    round_id=round_id,
                    action_id=f"IR-{round_id:02d}-STOP",
                    tool_id="none",
                    status="skipped",
                    validation_status="accepted",
                    detail=stop_reason,
                    metadata={
                        "plan_artifact": str(plan_path),
                        "agent_rationale": plan_result.data.get("agent_rationale") or [],
                    },
                ),
            )
            break

        new_artifact_count = 0
        for action in actions:
            signature = action.signature()
            record_base = {
                "round_id": round_id,
                "action_id": action.action_id,
                "tool_id": action.tool_id,
                "hypothesis": action.hypothesis,
                "expected_evidence_type": action.expected_evidence_type,
                "params": action.params,
                "depends_on_artifacts": action.depends_on_artifacts,
                "metadata": {
                    "signature": signature,
                    "plan_artifact": str(plan_path),
                    "agent_rationale": plan_result.data.get("agent_rationale") or [],
                },
            }
            if signature in seen_signatures:
                append_investigation_record(
                    workdir,
                    InvestigationRecord(
                        **record_base,
                        status="skipped",
                        validation_status="rejected",
                        detail="Duplicate tool_id + params + depends_on_artifacts action.",
                    ),
                )
                continue
            missing_artifacts = [
                artifact for artifact in action.depends_on_artifacts if not artifact_exists(workdir, artifact)
            ]
            if missing_artifacts:
                append_investigation_record(
                    workdir,
                    InvestigationRecord(
                        **record_base,
                        status="skipped",
                        validation_status="rejected",
                        detail=f"Missing depends_on_artifacts: {missing_artifacts}",
                    ),
                )
                seen_signatures.add(signature)
                continue

            step, output_artifacts = run_investigation_tool_action(
                action=action,
                workdir=workdir,
                source_data_dir=source_data_dir,
                env=env,
                force=force,
                progress=progress,
            )
            steps.append(step)
            new_artifact_count += sum(1 for artifact in output_artifacts if Path(artifact).exists())
            append_investigation_record(
                workdir,
                InvestigationRecord(
                    **record_base,
                    status=step.status,
                    validation_status="accepted",
                    output_artifacts=output_artifacts,
                    detail=step.detail,
                    command=step.command,
                ),
            )
            seen_signatures.add(signature)

        if new_artifact_count == 0:
            stop_reason = "no_new_artifacts"
            break

    manifest["stop_reason"] = stop_reason or "max_rounds_reached"
    manifest["records"] = read_investigation_records(workdir)
    return steps, manifest


def run_investigation_tool_action(
    *,
    action: InvestigationAction,
    workdir: Path,
    source_data_dir: Path | None,
    env: dict[str, str],
    force: bool,
    progress: ProgressCallback | None,
) -> tuple[StepResult, list[str]]:
    action_dir = workdir / "investigation" / f"round_{action.round_id:02d}" / safe_action_dir_name(action.action_id)
    action_dir.mkdir(parents=True, exist_ok=True)
    key = f"investigation_{action.round_id:02d}_{safe_action_dir_name(action.action_id)}"

    if action.tool_id in {"source_data.profile", SOURCE_DATA_FINDINGS_TOOL_ID, SOURCE_DATA_PAIR_FORENSICS_TOOL_ID}:
        if not source_data_dir or not source_data_dir.is_dir():
            step = StepResult(key, "Agent Investigation Tool", "skipped", "No selected Source Data directory.")
            emit_step_result(progress, step)
            return step, []

    if action.tool_id == "source_data.profile":
        output = action_dir / "source_data_profile.json"
        command = [
            sys.executable,
            "-m",
            "engine.static_audit.tools.source_data_profile",
            str(source_data_dir),
            "--output",
            str(output),
        ]
    elif action.tool_id == SOURCE_DATA_FINDINGS_TOOL_ID:
        profile = workdir / "source_data_profile.json"
        if not profile.exists():
            step = StepResult(key, "Agent Investigation Tool", "skipped", "source_data_profile.json missing.")
            emit_step_result(progress, step)
            return step, []
        output = action_dir / "source_data_findings.json"
        params = dict(DEFAULT_SOURCE_FINDING_PARAMS)
        params.update(action.params)
        command = [
            sys.executable,
            "-m",
            "engine.static_audit.tools.source_data_findings",
            str(source_data_dir),
            "--profile",
            str(profile),
            "--output",
            str(output),
            "--min-overlap",
            str(params["min_overlap"]),
            "--min-support",
            str(params["min_support"]),
            "--max-findings-per-category",
            str(params["max_findings_per_category"]),
        ]
        if (workdir / "full.md").exists():
            command.extend(["--full-md", str(workdir / "full.md")])
    elif action.tool_id == SOURCE_DATA_PAIR_FORENSICS_TOOL_ID:
        output = action_dir / "source_data_pair_forensics.json"
        params = action.params
        command = [
            sys.executable,
            "-m",
            "engine.static_audit.tools.source_data_pair_forensics",
            str(source_data_dir),
            "--output",
            str(output),
            "--min-pairs",
            str(params.get("min_pairs", 8)),
            "--min-support",
            str(params.get("min_support", 0.95)),
            "--ratio-places",
            str(params.get("ratio_places", 4)),
            "--max-offset",
            str(params.get("max_offset", 80)),
            "--max-findings-per-category",
            str(params.get("max_findings_per_category", 50)),
            "--min-duplicate-row-width",
            str(params.get("min_duplicate_row_width", 2)),
        ]
    elif action.tool_id == IMAGE_SIMILARITY_TOOL_ID:
        images_dir = workdir / "images"
        if not images_dir.is_dir():
            step = StepResult(key, "Agent Investigation Tool", "skipped", "images directory missing.")
            emit_step_result(progress, step)
            return step, []
        output = action_dir / "image_similarity_candidates.json"
        params = action.params
        command = [
            sys.executable,
            "-m",
            "engine.static_audit.tools.image_similarity",
            str(images_dir),
            "--output",
            str(output),
            "--max-distance",
            str(params.get("max_distance", 8)),
            "--max-candidates",
            str(params.get("max_candidates", 200)),
        ]
    else:
        step = StepResult(key, "Agent Investigation Tool", "skipped", f"Unsupported action tool_id: {action.tool_id}")
        emit_step_result(progress, step)
        return step, []

    step = run_command(
        key,
        f"Agent Investigation Tool: {action.tool_id}",
        command,
        [output],
        cwd=PROJECT_ROOT,
        env=env,
        force=force,
        progress=progress,
    )
    return step, [str(output)]


def brief_list(items: Any, limit: int = 8) -> str:
    if not isinstance(items, list) or not items:
        return "-"
    return ", ".join(str(item) for item in items[:limit])


def agent_manual_review_rows(tasks: list[dict[str, Any]], limit: int = 12) -> list[list[str]]:
    rows = []
    for task in tasks[:limit]:
        refs = task.get("evidence_refs") or []
        rows.append(
            [
                task.get("task_id", "-"),
                task.get("priority", "-"),
                str(task.get("question", "-"))[:220],
                ", ".join(str(item) for item in refs if item) or "-",
            ]
        )
    return rows


def agent_finding_review_rows(reviews: list[dict[str, Any]], limit: int = 12) -> list[list[str]]:
    rows = []
    for review in reviews[:limit]:
        rows.append(
            [
                review.get("finding_id", "-"),
                review.get("assessment", "-"),
                review.get("residual_risk", "-"),
                brief_list(review.get("benign_explanations"), 3),
            ]
        )
    return rows


def investigation_record_rows(records: list[dict[str, Any]], limit: int = 20) -> list[list[str]]:
    rows = []
    for record in records[:limit]:
        artifacts = record.get("output_artifacts") or []
        rows.append(
            [
                record.get("round_id", "-"),
                record.get("action_id", "-"),
                record.get("tool_id", "-"),
                record.get("status", "-"),
                str(record.get("hypothesis") or record.get("detail") or "-")[:180],
                brief_list(artifacts, 3),
            ]
        )
    return rows


def generate_report(
    *,
    paper_dir: Path,
    paper_pdf: Path,
    source_data_dir: Path | None,
    workdir: Path,
    case_id: str,
    agent_mode: str,
    steps: list[StepResult],
) -> Path:
    mineru_manifest = read_json(workdir / "mineru_manifest.json")
    material_inventory = read_json(workdir / "material_inventory.json")
    material_plan = read_json(workdir / "agent_material_plan.json")
    ledger = read_json(workdir / "evidence_ledger.json")
    numeric = read_json(workdir / "numeric_forensics.json")
    profile = read_json(workdir / "source_data_profile.json")
    findings = read_json(workdir / "source_data_findings.json")
    pair_forensics = read_json(workdir / "source_data_pair_forensics.json")
    duplicates = read_json(workdir / "exact_image_duplicates.json")
    similarity = read_json(workdir / "image_similarity_candidates.json")
    investigation_records = read_investigation_records(workdir)
    static_bundle = read_json(workdir / "static_audit_bundle.json")
    vlm = read_json(workdir / "vlm_triage_selected.json")
    agent_plan = read_json(workdir / "agent_audit_plan.json") if agent_mode in {"plan", "full"} else None
    agent_review = read_json(workdir / "agent_review.json") if agent_mode in {"review", "full"} else None

    lines: list[str] = []
    lines.append(f"# Veritas Paper Audit Report: {case_id}")
    lines.append("")
    lines.append("## 结论先行")
    lines.append("")
    lines.append("- 本报告由本地 orchestrator 汇总确定性脚本产物生成。")
    if agent_mode != "off":
        lines.append("- opencode Agent 作为编排与结构化审阅层参与：前置选择/参数填充，后置 claim/finding 复核。")
    lines.append("- 当前不做最终科研诚信判定，只报告技术事实候选、材料缺口和人工复核入口。")
    lines.append("- PDF 是发表呈现层；Source Data、代码、环境和结果文件才是更高价值证据层。")
    if not source_data_dir:
        lines.append("- 当前未选择可执行 XLSX/XLSM Source Data optional lane，Source Data 审查被标记为材料缺口或暂不支持。")
    if not vlm:
        lines.append("- 当前未执行批量 VLM 视觉审查；视觉结论仅限已有抽样或未覆盖。")
    lines.append("")

    lines.append("## Scope")
    lines.append("")
    lines.append(markdown_table(
        ["Item", "Value"],
        [
            ["case_id", case_id],
            ["paper_dir", paper_dir],
            ["paper_pdf", paper_pdf],
            ["selected_source_data_dir", source_data_dir or "not_selected"],
            ["material_inventory", workdir / "material_inventory.json"],
            ["agent_material_plan", workdir / "agent_material_plan.json"],
            ["workdir", workdir],
            ["agent_mode", agent_mode],
            ["generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")],
        ],
    ))
    lines.append("")

    lines.append("## Pipeline Execution")
    lines.append("")
    lines.append(markdown_table(
        ["Step", "Status", "Detail"],
        [[step.title, step.status, step.detail.replace("\n", " ")[:240]] for step in steps],
    ))
    lines.append("")

    lines.append("## Artifact Manifest")
    lines.append("")
    artifact_rows = []
    for name in [
        "mineru_manifest.json",
        "full.md",
        "material_inventory.json",
        "agent_material_plan.json",
        "evidence_ledger.json",
        "numeric_forensics.json",
        "source_data_profile.json",
        "source_data_findings.json",
        "source_data_pair_forensics.json",
        "exact_image_duplicates.json",
        "vlm_triage_selected.json",
        "agent_audit_plan.json",
        "agent_review.json",
        "agent_claim_extractor.json",
        "agent_source_data_auditor.json",
        "agent_visual_triage.json",
        "agent_digit_pattern.json",
        "agent_math_consistency.json",
        "agent_domain_sanity.json",
        "agent_defense.json",
        "agent_judge.json",
        "image_similarity_candidates.json",
        "investigation_rounds.jsonl",
        "static_audit_bundle.json",
        "final_audit_report.html",
    ]:
        path = workdir / name
        artifact_rows.append([name, "present" if path.exists() else "missing", path.stat().st_size if path.exists() else "-"])
    lines.append(markdown_table(["Artifact", "Status", "Bytes"], artifact_rows))
    lines.append("")

    if material_inventory or material_plan:
        inventory_summary = (material_inventory or {}).get("summary", {})
        material_by_type = inventory_summary.get("by_material_type") if isinstance(inventory_summary.get("by_material_type"), dict) else {}
        selected_lanes = (material_plan or {}).get("selected_optional_lanes") if isinstance((material_plan or {}).get("selected_optional_lanes"), list) else []
        selected_lane_text = brief_list(
            [
                f"{lane.get('lane_id')}:{lane.get('status')}:{lane.get('root') or '-'}"
                for lane in selected_lanes
                if isinstance(lane, dict)
            ],
            limit=5,
        )
        lines.append("## Material Inventory and Optional Lanes")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["material_files", fmt_int(inventory_summary.get("file_count"))],
                ["material_types", ", ".join(f"{key}={value}" for key, value in material_by_type.items()) or "-"],
                ["candidate_source_roots", fmt_int(inventory_summary.get("candidate_source_roots"))],
                ["supported_optional_lanes", fmt_int(inventory_summary.get("supported_optional_lanes"))],
                ["material_plan_status", (material_plan or {}).get("status", "ok")],
                ["selected_optional_lanes", selected_lane_text],
                ["missing_materials", brief_list((material_plan or {}).get("missing_materials"))],
            ],
        ))
        unsupported = (material_plan or {}).get("unsupported_materials") or []
        if unsupported:
            lines.append("")
            lines.append("Unsupported optional materials detected:")
            for item in unsupported[:8]:
                if isinstance(item, dict):
                    lines.append(f"- `{item.get('path', '-')}` ({item.get('material_type', '-')})")
        lines.append("")

    if investigation_records:
        lines.append("## Agent Investigation Path")
        lines.append("")
        lines.append(
            "- 本节展示 AgentInvestigationPlanner 的受控调查路径；Agent 只选择 Tool Registry 允许的确定性工具，实际执行由 orchestrator 完成。"
        )
        lines.append(markdown_table(
            ["Round", "Action", "Tool", "Status", "Hypothesis", "Artifacts"],
            investigation_record_rows(investigation_records),
        ))
        lines.append("")

    if agent_plan:
        params = source_finding_params_from_plan(agent_plan)
        selected_tool_ids = selected_tool_ids_from_plan(agent_plan)
        lines.append("## Agent Audit Plan Summary")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["status", agent_plan.get("status", "ok")],
                ["selected_tools", brief_list(selected_tool_ids)],
                ["selected_steps", brief_list(agent_plan.get("selected_steps"))],
                ["missing_materials", brief_list(agent_plan.get("missing_materials"))],
                ["source_data_findings.min_overlap", fmt_int(params.get("min_overlap"))],
                ["source_data_findings.min_support", fmt_float(params.get("min_support"), 3)],
                ["source_data_findings.max_findings_per_category", fmt_int(params.get("max_findings_per_category"))],
            ],
        ))
        rationale = agent_plan.get("agent_rationale") or []
        if rationale:
            lines.append("")
            lines.append("Agent rationale:")
            for item in rationale[:6]:
                lines.append(f"- {item}")
        lines.append("")

    if agent_review:
        lines.append("## Agent Review")
        lines.append("")
        candidate_claims = agent_review.get("candidate_claims") or []
        mapping_reviews = agent_review.get("claim_to_source_data") or []
        finding_reviews = agent_review.get("finding_reviews") or []
        manual_tasks = agent_review.get("manual_review_tasks") or []
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["status", agent_review.get("status", "ok")],
                ["candidate_claims", fmt_int(len(candidate_claims))],
                ["claim_to_source_data_reviews", fmt_int(len(mapping_reviews))],
                ["finding_reviews", fmt_int(len(finding_reviews))],
                ["manual_review_tasks", fmt_int(len(manual_tasks))],
            ],
        ))
        if manual_tasks:
            lines.append("")
            lines.append("### Agent Manual Review Tasks")
            lines.append("")
            lines.append(markdown_table(
                ["Task", "Priority", "Question", "Evidence Refs"],
                agent_manual_review_rows(manual_tasks),
            ))
        if finding_reviews:
            lines.append("")
            lines.append("### Agent Finding Reviews")
            lines.append("")
            lines.append(markdown_table(
                ["Finding", "Assessment", "Residual Risk", "Benign Explanations"],
                agent_finding_review_rows(finding_reviews),
            ))
        notes = agent_review.get("report_notes") or []
        if notes:
            lines.append("")
            lines.append("Agent report notes:")
            for item in notes[:8]:
                lines.append(f"- {item}")
        lines.append("")

    if static_bundle and (static_bundle.get("claim_mappings") or []):
        bundle_claims = static_bundle.get("claims") or []
        bundle_mappings = static_bundle.get("claim_mappings") or []
        mapping_policy = ((static_bundle.get("metadata") or {}).get("claim_mapping_policy") or {})
        lines.append("## Canonical Claim-to-source-data Mapping")
        lines.append("")
        lines.append(
            "- 该表优先展示 Agent refined mapping；确定性 Source Data mapping 保留为 provenance scaffolding。"
        )
        lines.append(
            f"- canonical_preference: `{mapping_policy.get('canonical_preference', 'agent_refined')}`; "
            f"fallback: `{mapping_policy.get('fallback', 'deterministic_scaffolding')}`。"
        )
        lines.append("")
        lines.append(markdown_table(
            ["Mapping", "Claim", "Claim Text", "Confidence", "Status", "Source Data Refs"],
            canonical_claim_mapping_rows(bundle_claims, bundle_mappings),
        ))
        lines.append("")

    if ledger:
        stats = ledger.get("stats", {})
        lines.append("## Evidence Ledger Summary")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["pages", fmt_int(stats.get("pages"))],
                ["markdown_lines", fmt_int(stats.get("markdown_lines"))],
                ["content_blocks", fmt_int(stats.get("content_blocks"))],
                ["tables", fmt_int(stats.get("tables"))],
                ["figures", fmt_int(stats.get("figures"))],
                ["images", fmt_int(stats.get("images"))],
                ["captions", fmt_int(stats.get("captions"))],
                ["cells", fmt_int(stats.get("cells"))],
                ["ledger_items", fmt_int(stats.get("ledger_items"))],
            ],
        ))
        warnings = ledger.get("warnings") or []
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for warning in warnings:
                lines.append(f"- `{warning.get('code')}`: {warning.get('message')}")
        lines.append("")

    if numeric:
        benford = numeric.get("benford", {})
        lines.append("## Numeric Forensics Summary")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["all_number_count", fmt_int(numeric.get("all_number_count"))],
                ["effective_number_count", fmt_int(numeric.get("number_count"))],
                ["table_count", fmt_int(numeric.get("table_count"))],
                ["effective_scope", numeric.get("effective_scope", "-")],
                ["benford_applicability", benford.get("applicability", "-")],
                [
                    "benford_mad",
                    fmt_float(
                        benford.get("mad", benford.get("mean_absolute_deviation")),
                        4,
                    ),
                ],
            ],
        ))
        lines.append("")
        lines.append("Interpretation: PDF numeric forensics is treated as audit leads, not as final evidence. OCR/table extraction artifacts must be excluded before escalation.")
        lines.append("")

    if profile:
        summary = profile.get("summary", {})
        lines.append("## Source Data Profile")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["workbook_count", fmt_int(summary.get("workbook_count"))],
                ["sheet_count", fmt_int(summary.get("sheet_count"))],
                ["cell_count", fmt_int(summary.get("cell_count"))],
                ["numeric_cell_count", fmt_int(summary.get("numeric_cell_count"))],
                ["formula_count", fmt_int(summary.get("formula_count"))],
                ["terminal_0_or_5_rate", fmt_float(summary.get("terminal_0_or_5_rate"), 3)],
                ["workbooks_with_errors", ", ".join(summary.get("workbooks_with_errors") or []) or "-"],
            ],
        ))
        lines.append("")

    if findings:
        summary = findings.get("summary", {})
        priority = findings.get("priority_findings") or []
        lines.append("## Source Data Findings")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["duplicate_column_findings", fmt_int(summary.get("duplicate_column_findings"))],
                ["fixed_relationship_findings", fmt_int(summary.get("fixed_relationship_findings"))],
                ["formula_derived_columns", fmt_int(summary.get("formula_derived_columns"))],
                ["claim_to_source_data_mappings", fmt_int(summary.get("claim_to_source_data_mappings"))],
                ["priority_findings", fmt_int(summary.get("priority_findings"))],
                ["errors", fmt_int(summary.get("errors"))],
            ],
        ))
        lines.append("")
        if priority:
            lines.append("### Priority Findings")
            lines.append("")
            lines.append(markdown_table(
                ["ID", "Risk", "Workbook", "Sheet", "Columns", "Relation", "Support"],
                [priority_row(item) for item in priority],
            ))
            lines.append("")
            lines.append("These are manual-review candidates, not misconduct conclusions.")
            lines.append("")

        mappings = findings.get("claim_to_source_data") or []
        if mappings:
            lines.append("### Deterministic Claim-to-source-data Scaffolding")
            lines.append("")
            lines.append("该表由脚本按 Source Data sheet 名称和论文 figure 引用生成，用作 Agent 复核的候选脚手架，不作为最终主视图。")
            lines.append("")
            lines.append(markdown_table(
                ["Mapping", "Figure", "Workbook", "Sheet", "Priority", "Linked Findings", "Candidate Claim"],
                claim_mapping_rows(mappings),
            ))
            lines.append("")

    if pair_forensics:
        summary = pair_forensics.get("summary", {})
        priority = pair_forensics.get("priority_findings") or []
        lines.append("## Source Data Pair / Row-Offset Forensics")
        lines.append("")
        lines.append("该工具检查通用的 paired cohort、前后半区、固定行偏移、低宽度行重复和比例复用模式；它不依赖特定论文或 PubPeer 评论。")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["findings", fmt_int(summary.get("findings"))],
                ["priority_findings", fmt_int(summary.get("priority_findings"))],
                ["row_offset_scalar_findings", fmt_int(summary.get("row_offset_scalar_findings"))],
                ["paired_ratio_reuse_findings", fmt_int(summary.get("paired_ratio_reuse_findings"))],
                ["duplicate_row_vector_findings", fmt_int(summary.get("duplicate_row_vector_findings"))],
                ["rounding_bias_findings", fmt_int(summary.get("rounding_bias_findings"))],
                ["errors", fmt_int(summary.get("errors"))],
            ],
        ))
        lines.append("")
        if priority:
            lines.append("### Pair Forensics Priority Findings")
            lines.append("")
            lines.append(markdown_table(
                ["ID", "Risk", "Category", "Workbook", "Sheet", "Offset", "Columns", "Support"],
                pair_forensics_rows(priority),
            ))
            lines.append("")
            lines.append("这些是样本独立性和数据派生模式的人工复核入口，不是最终诚信判定。")
            lines.append("")

    if duplicates:
        lines.append("## Image Duplicate Check")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["image_count", fmt_int(duplicates.get("image_count"))],
                ["duplicate_group_count", fmt_int(duplicates.get("duplicate_group_count"))],
                ["duplicate_image_count", fmt_int(duplicates.get("duplicate_image_count"))],
            ],
        ))
        lines.append("")
        lines.append("Byte-identical duplicate checking cannot detect crops, rescaling, rotations, contrast changes, or local reuse.")
        lines.append("")

    if similarity:
        lines.append("## Image Similarity Candidates")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["status", similarity.get("status", "-")],
                ["method", similarity.get("method", "-")],
                ["image_count", fmt_int(similarity.get("image_count"))],
                ["candidate_count", fmt_int(similarity.get("candidate_count"))],
            ],
        ))
        if similarity.get("status") == "not_available":
            lines.append("")
            lines.append("Near-duplicate image triage was not available in this environment; deterministic exact duplicate checking still ran.")
        lines.append("")

    if static_bundle:
        traces = static_bundle.get("agent_traces") or []
        evidence_items = static_bundle.get("evidence_items") or []
        lines.append("## Static Audit Bundle")
        lines.append("")
        lines.append(markdown_table(
            ["Metric", "Value"],
            [
                ["protocol_version", static_bundle.get("protocol_version", "-")],
                ["evidence_items", fmt_int(len(evidence_items))],
                ["claims", fmt_int(len(static_bundle.get("claims") or []))],
                ["findings", fmt_int(len(static_bundle.get("findings") or []))],
                ["claim_mappings", fmt_int(len(static_bundle.get("claim_mappings") or []))],
                ["agent_traces", fmt_int(len(traces))],
                ["execution_status", (static_bundle.get("execution_status") or {}).get("status", "-")],
            ],
        ))
        if traces:
            lines.append("")
            lines.append("### Role Trace Summary")
            lines.append("")
            lines.append(markdown_table(
                ["Role", "Status", "Output", "Detail"],
                [
                    [
                        trace.get("role_id", "-"),
                        trace.get("status", "-"),
                        trace.get("output_path", "-"),
                        str(trace.get("detail", "-"))[:160],
                    ]
                    for trace in traces
                ],
            ))
        lines.append("")

    if vlm:
        lines.append("## VLM Triage")
        lines.append("")
        lines.append("- Existing VLM triage artifact detected: `vlm_triage_selected.json`.")
        lines.append("- Current orchestrator does not run batch VLM review yet; existing VLM output is treated as non-primary triage evidence.")
        lines.append("")

    lines.append("## Limitations")
    lines.append("")
    limitations = [
        "This run does not make a final research-integrity judgment.",
        "Claim-to-source-data mapping is currently sheet/figure level unless manually refined to panel/column-block level.",
        "VLM image review is not yet a complete batch pipeline.",
        "Code-execution verification is not connected for this paper directory unless a code repo and manifest are supplied.",
    ]
    if mineru_manifest and not any(workdir.glob("*_middle.json")):
        limitations.append("MinerU middle JSON may be missing; layout/bbox confidence should be lowered.")
    if agent_mode in {"plan", "full"} and not agent_plan:
        limitations.append("opencode Agent plan artifact is missing; deterministic defaults were used.")
    if material_plan and material_plan.get("status") in {"fallback", "deterministic_fallback"}:
        limitations.append(f"Material optional-lane planning used fallback mode: {material_plan.get('detail', '-')}")
    if material_plan and material_plan.get("unsupported_materials"):
        limitations.append("Some submitted materials were inventoried but not executable in this MVP optional-lane set.")
    if agent_mode in {"review", "full"} and not agent_review:
        limitations.append("opencode Agent review artifact is missing; claim/finding interpretation is deterministic-only.")
    if agent_plan and agent_plan.get("status") == "failed":
        limitations.append(f"opencode Agent plan failed: {agent_plan.get('detail', '-')}")
    if agent_review and agent_review.get("status") == "failed":
        limitations.append(f"opencode Agent review failed: {agent_review.get('detail', '-')}")
    for item in (agent_review or {}).get("limitations", [])[:6]:
        limitations.append(f"Agent review limitation: {item}")
    for item in limitations:
        lines.append(f"- {item}")
    lines.append("")

    report_path = workdir / "final_audit_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_static_audit_bundle(
    *,
    paper_dir: Path,
    paper_pdf: Path,
    source_data_dir: Path | None,
    workdir: Path,
    case_id: str,
    steps: list[StepResult],
    agent_manifest: dict[str, Any],
) -> StaticAuditBundle:
    evidence_items = collect_evidence_items(workdir)
    claims, claim_mappings, findings = collect_claims_and_findings(workdir, evidence_items)
    traces = collect_agent_traces(workdir, agent_manifest)
    return StaticAuditBundle(
        case_id=case_id,
        inputs={
            "paper_dir": str(paper_dir),
            "paper_pdf": str(paper_pdf),
            "source_data_dir": str(source_data_dir) if source_data_dir else None,
            "material_inventory": str(workdir / "material_inventory.json"),
            "agent_material_plan": str(workdir / "agent_material_plan.json"),
            "optional_lanes": agent_manifest.get("optional_lanes", []),
            "workdir": str(workdir),
        },
        tool_runs=[
            ToolRun(
                tool_id=STEP_TOOL_IDS.get(step.key, step.key),
                step_key=step.key,
                status=step.status,  # type: ignore[arg-type]
                title=step.title,
                command=step.command,
                outputs=[],
                detail=step.detail,
            )
            for step in steps
        ],
        evidence_items=evidence_items,
        claims=claims,
        findings=findings,
        claim_mappings=claim_mappings,
        agent_traces=traces,
        limitations=[
            "Static audit bundle v1 is generated from deterministic artifacts and current Agent review output.",
            "Code execution audit is not connected in this static-audit run.",
        ],
        execution_status=ExecutionStatus(status="not_provided"),
        metadata={
            "agent": agent_manifest,
            "investigation_records": read_investigation_records(workdir),
            "material_plan": read_json(workdir / "agent_material_plan.json") or {},
            "claim_mapping_policy": {
                "canonical_preference": "agent_refined",
                "fallback": "deterministic_scaffolding",
                "deterministic_scaffolding_artifact": str(workdir / "source_data_findings.json"),
                "agent_claim_artifact": str(workdir / "agent_claim_extractor.json"),
                "agent_source_data_artifact": str(workdir / "agent_source_data_auditor.json"),
            },
            "deterministic_claim_mappings": (
                (read_json(workdir / "source_data_findings.json") or {}).get("claim_to_source_data")
                or []
            ),
        },
    )


def collect_evidence_items(workdir: Path) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for name, kind in [
        ("full.md", "output_artifact"),
        ("material_inventory.json", "output_artifact"),
        ("agent_material_plan.json", "output_artifact"),
        ("evidence_ledger.json", "output_artifact"),
        ("numeric_forensics.json", "output_artifact"),
        ("paperfraud_rule_matches.json", "output_artifact"),
        ("source_data_profile.json", "output_artifact"),
        ("source_data_findings.json", "output_artifact"),
        ("source_data_pair_forensics.json", "output_artifact"),
        ("exact_image_duplicates.json", "output_artifact"),
        ("image_similarity_candidates.json", "output_artifact"),
        ("investigation_rounds.jsonl", "output_artifact"),
    ]:
        path = workdir / name
        if path.exists():
            items.append(
                EvidenceItem(
                    evidence_id=f"EV-ART-{len(items) + 1:04d}",
                    kind=kind,  # type: ignore[arg-type]
                    source_path=str(path),
                    summary=f"Audit artifact: {name}",
                    metadata={"bytes": path.stat().st_size},
                )
            )

    source_findings = read_json(workdir / "source_data_findings.json") or {}
    for finding in (source_findings.get("priority_findings") or [])[:100]:
        items.append(
            EvidenceItem(
                evidence_id=f"EV-SD-{len(items) + 1:04d}",
                kind="sheet",
                source_path=str(finding.get("workbook", "")),
                locator={
                    "sheet": finding.get("sheet"),
                    "columns": finding.get("column_pair"),
                    "support_rows": finding.get("support_rows") or finding.get("equal_rows"),
                    "overlap_rows": finding.get("overlap_rows"),
                },
                summary=f"Source Data priority finding {finding.get('finding_id')}",
                metadata={"finding_id": finding.get("finding_id")},
            )
        )
    pair_forensics = read_json(workdir / "source_data_pair_forensics.json") or {}
    for finding in (pair_forensics.get("priority_findings") or [])[:100]:
        items.append(
            EvidenceItem(
                evidence_id=f"EV-PF-{len(items) + 1:04d}",
                kind="sheet",
                source_path=str(finding.get("workbook", "")),
                locator={
                    "sheet": finding.get("sheet"),
                    "row_offset": finding.get("row_offset"),
                    "columns": finding.get("columns") or finding.get("column_pair") or finding.get("column"),
                    "support_rows": finding.get("support_rows") or finding.get("matched_pairs") or finding.get("duplicate_row_count"),
                    "overlap_rows": finding.get("overlap_rows") or finding.get("overlap_pairs"),
                },
                summary=f"Source Data pair-forensics finding {finding.get('finding_id')}",
                metadata={"finding_id": finding.get("finding_id"), "source": "source_data_pair_forensics"},
            )
        )
    return items


def collect_claims_and_findings(
    workdir: Path,
    evidence_items: list[EvidenceItem],
) -> tuple[list[Claim], list[ClaimMapping], list[Finding]]:
    source_findings = read_json(workdir / "source_data_findings.json") or {}
    pair_forensics = read_json(workdir / "source_data_pair_forensics.json") or {}
    deterministic_mappings = source_findings.get("claim_to_source_data") or []
    agent_claims = read_json(workdir / "agent_claim_extractor.json") or {}
    agent_source = read_json(workdir / "agent_source_data_auditor.json") or {}
    evidence_by_finding = {
        item.metadata.get("finding_id"): item.evidence_id
        for item in evidence_items
        if item.metadata.get("finding_id")
    }

    claims, mappings = collect_agent_refined_claim_mappings(
        agent_claims=agent_claims,
        agent_source=agent_source,
        deterministic_mappings=deterministic_mappings,
    )
    if not claims and not mappings:
        claims, mappings = collect_deterministic_claim_mappings(
            source_findings=source_findings,
            evidence_by_finding=evidence_by_finding,
        )

    findings: list[Finding] = []
    paperfraud_matches = read_json(workdir / "paperfraud_rule_matches.json") or {}
    findings.extend(paperfraud_findings_from_matches(paperfraud_matches))
    for item in source_findings.get("priority_findings") or []:
        finding_id = str(item.get("finding_id"))
        findings.append(
            Finding(
                finding_id=finding_id,
                category=str(item.get("category", "")),
                risk_level=str(item.get("risk_level", "medium")),  # type: ignore[arg-type]
                summary=f"{item.get('category')} in {item.get('workbook')} / {item.get('sheet')}",
                evidence_refs=[evidence_by_finding[finding_id]] if finding_id in evidence_by_finding else [],
                benign_explanations=[str(value) for value in (item.get("benign_explanations") or [])],
                pressure_test_result=str(item.get("pressure_test_result", "")),
                manual_review_note=str(item.get("manual_review_note", "")),
                metadata=item,
            )
        )
    for item in pair_forensics.get("priority_findings") or []:
        finding_id = str(item.get("finding_id"))
        evidence_id = evidence_by_finding.get(finding_id)
        findings.append(
            Finding(
                finding_id=finding_id,
                category=str(item.get("category", "")),
                risk_level=str(item.get("risk_level", "medium")),  # type: ignore[arg-type]
                summary=(
                    f"{item.get('category')} in {item.get('workbook')} / {item.get('sheet')} "
                    f"offset={item.get('row_offset', '-')}"
                ),
                evidence_refs=[evidence_id] if evidence_id else [],
                benign_explanations=[str(value) for value in (item.get("benign_explanations") or [])],
                pressure_test_result=str(item.get("pressure_test_result", "")),
                manual_review_note="Pair/row-offset Source Data pattern requires sample-independence review.",
                metadata={**item, "source_artifact": "source_data_pair_forensics.json"},
            )
        )
    return claims, mappings, findings


def collect_agent_refined_claim_mappings(
    *,
    agent_claims: dict[str, Any],
    agent_source: dict[str, Any],
    deterministic_mappings: list[dict[str, Any]],
) -> tuple[list[Claim], list[ClaimMapping]]:
    claim_items = [item for item in (agent_claims.get("claims") or []) if isinstance(item, dict)]
    source_items = [item for item in (agent_source.get("claim_to_source_data") or []) if isinstance(item, dict)]
    if not claim_items and not source_items:
        return [], []

    deterministic_by_id = {
        str(item.get("mapping_id")): item
        for item in deterministic_mappings
        if isinstance(item, dict) and item.get("mapping_id")
    }

    claims: list[Claim] = []
    claims_by_id: dict[str, Claim] = {}
    for index, item in enumerate(claim_items[:200], start=1):
        claim_text = item.get("claim_text") or item.get("text")
        if not claim_text:
            continue
        claim_id = str(item.get("claim_id") or f"AC-{index:03d}")
        claim = Claim(
            claim_id=claim_id,
            text=str(claim_text),
            claim_type=str(item.get("claim_type", "figure_trace")),
            source=str(item.get("paper_location", "")),
            evidence_refs=[str(ref) for ref in (item.get("evidence_refs") or [])],
            status=normalize_claim_status(item.get("status")),
            metadata={
                "source_role": "claim_extractor",
                "canonical_source": "agent_refined",
                "agent_status": item.get("status"),
                "raw": item,
            },
        )
        claims.append(claim)
        claims_by_id[claim_id] = claim

    mappings: list[ClaimMapping] = []
    for index, item in enumerate(source_items[:200], start=1):
        claim_id = str(item.get("claim_id") or f"ACM-{index:03d}")
        if claim_id not in claims_by_id:
            claim = Claim(
                claim_id=claim_id,
                text="Agent SourceDataAuditor 生成了映射，但 ClaimExtractor 未提供对应 claim 文本。",
                claim_type="figure_trace",
                source="agent_source_data_auditor",
                evidence_refs=[str(ref) for ref in (item.get("source_data_refs") or [])],
                status="warning",
                metadata={
                    "source_role": "source_data_auditor",
                    "canonical_source": "agent_refined_placeholder",
                    "raw": item,
                },
            )
            claims.append(claim)
            claims_by_id[claim_id] = claim
        deterministic_mapping_id = item.get("mapping_id")
        deterministic_mapping = (
            deterministic_by_id.get(str(deterministic_mapping_id))
            if deterministic_mapping_id
            else None
        )
        mappings.append(
            ClaimMapping(
                mapping_id=str(item.get("mapping_id") or f"ACM-{index:03d}"),
                claim_id=claim_id,
                evidence_refs=[str(ref) for ref in (item.get("source_data_refs") or [])],
                confidence=str(item.get("confidence", "medium")),
                status="agent_refined_mapping",
                rationale="SourceDataAuditor refined deterministic Source Data scaffolding into a review-oriented claim mapping.",
                metadata={
                    "source_role": "source_data_auditor",
                    "canonical_source": "agent_refined",
                    "needs_human_review": bool(item.get("needs_human_review", True)),
                    "source_data_refs": [str(ref) for ref in (item.get("source_data_refs") or [])],
                    "deterministic_mapping": deterministic_mapping,
                    "raw": item,
                },
            )
        )
    return claims, mappings


def normalize_claim_status(value: Any) -> Status:
    allowed = {
        "pending",
        "ran",
        "reused",
        "skipped",
        "warning",
        "failed",
        "not_run",
        "not_provided",
        "missing_material",
    }
    status = str(value or "pending")
    return status if status in allowed else "pending"  # type: ignore[return-value]


def collect_deterministic_claim_mappings(
    *,
    source_findings: dict[str, Any],
    evidence_by_finding: dict[Any, str],
) -> tuple[list[Claim], list[ClaimMapping]]:
    claims: list[Claim] = []
    mappings: list[ClaimMapping] = []
    for index, mapping in enumerate((source_findings.get("claim_to_source_data") or [])[:200], start=1):
        claim_items = mapping.get("candidate_claims") or []
        claim_text = claim_items[0].get("text") if claim_items and isinstance(claim_items[0], dict) else ""
        if not claim_text:
            continue
        claim_id = f"CL-{index:04d}"
        linked = [
            item.get("finding_id")
            for item in (mapping.get("linked_priority_findings") or [])
            if isinstance(item, dict) and item.get("finding_id")
        ]
        refs = [evidence_by_finding[item] for item in linked if item in evidence_by_finding]
        claims.append(
            Claim(
                claim_id=claim_id,
                text=claim_text,
                claim_type="figure_trace",
                source=str(mapping.get("source_figure_id", "")),
                evidence_refs=refs,
                status="pending",
                metadata={
                    "mapping_id": mapping.get("mapping_id"),
                    "canonical_source": "deterministic_scaffolding_fallback",
                },
            )
        )
        mappings.append(
            ClaimMapping(
                mapping_id=str(mapping.get("mapping_id") or f"CM-{index:04d}"),
                claim_id=claim_id,
                evidence_refs=refs,
                confidence=str(mapping.get("mapping_confidence", "medium")),
                finding_refs=linked,
                rationale=str(mapping.get("manual_review_note", "")),
                metadata={
                    "canonical_source": "deterministic_scaffolding_fallback",
                    "source_figure_id": mapping.get("source_figure_id"),
                    "workbook": mapping.get("workbook"),
                    "sheet": mapping.get("sheet"),
                    "review_priority": mapping.get("review_priority"),
                    "raw": mapping,
                },
            )
        )
    return claims, mappings


def run_agent_roles(
    *,
    case_id: str,
    workdir: Path,
    agent_enabled: bool,
    agent_mode: str,
    force: bool,
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
    progress: ProgressCallback | None = None,
) -> tuple[list[StepResult], list[dict[str, Any]]]:
    steps: list[StepResult] = []
    role_manifest: list[dict[str, Any]] = []
    for role in ROLE_DEFINITIONS:
        output_path = workdir / role.output_artifact
        trace_path = workdir / "agent_traces" / f"{role.role_id}.json"
        existing_trace = read_agent_trace(trace_path)
        if (
            not force
            and output_path.exists()
            and existing_trace is not None
            and existing_trace.status in {"ran", "skipped"}
        ):
            role_manifest.append(
                {
                    "role_id": role.role_id,
                    "status": "reused",
                    "output": str(output_path),
                    "trace": str(trace_path),
                    "previous_status": existing_trace.status,
                }
            )
            if role.real_in_v1:
                record_step(
                    steps,
                    StepResult(
                        f"agent_role_{role.role_id}",
                        f"opencode Agent role: {role.title}",
                        "reused",
                        "Existing successful role output and trace found.",
                    ),
                    progress,
                )
            continue
        if not role.real_in_v1:
            trace = skipped_trace(role, "Role schema reserved; not executed in static_audit_protocol.v1.")
            trace.output_path = str(output_path)
            write_reserved_role_output(workdir, role, trace)
            write_role_trace(workdir, trace)
            role_manifest.append({"role_id": role.role_id, "status": trace.status, "output": str(output_path)})
            continue

        step_key = f"agent_role_{role.role_id}"
        if not agent_enabled:
            trace = AgentTrace(
                role_id=role.role_id,
                status="not_run",
                input_artifacts=list(role.input_artifacts),
                output_path=str(output_path),
                output_summary={},
                model=model,
                detail=f"agent_mode={agent_mode} does not run static-audit role agents.",
            )
            write_reserved_role_output(workdir, role, trace)
            write_role_trace(workdir, trace)
            record_step(steps, StepResult(step_key, f"opencode Agent role: {role.title}", "skipped", trace.detail), progress)
            role_manifest.append({"role_id": role.role_id, "status": trace.status, "output": str(output_path)})
            continue

        emit_step_start(
            progress,
            step_key,
            f"opencode Agent role: {role.title}",
            f"Calling opencode role agent {role.role_id}.",
        )
        result = run_agent_role(
            role_id=role.role_id,
            case_id=case_id,
            workdir=workdir,
            project_root=project_root,
            env=env,
            model=model,
            opencode_bin=opencode_bin,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        payload = write_role_agent_result(output_path, role, case_id, result)
        trace = trace_from_role_result(role, output_path, result, payload, model)
        write_role_trace(workdir, trace)
        metadata = result_metadata(result, output_path)
        metadata["role_id"] = role.role_id
        role_manifest.append(metadata)
        record_step(
            steps,
            StepResult(
                step_key,
                f"opencode Agent role: {role.title}",
                agent_step_status(result.status),
                result.detail,
                result.command,
            ),
            progress,
        )
    return steps, role_manifest


def collect_agent_traces(workdir: Path, agent_manifest: dict[str, Any]) -> list[AgentTrace]:
    traces: list[AgentTrace] = []
    for role in ROLE_DEFINITIONS:
        trace_path = workdir / "agent_traces" / f"{role.role_id}.json"
        trace = read_agent_trace(trace_path)
        if trace is None:
            trace = skipped_trace(role, "Role trace was missing and has been backfilled.")
            trace.output_path = str(workdir / role.output_artifact)
            write_reserved_role_output(workdir, role, trace)
            write_role_trace(workdir, trace)
        traces.append(trace)
    return traces


def trace_from_role_result(
    role: RoleDefinition,
    output_path: Path,
    result: AgentRunResult,
    payload: dict[str, Any],
    model: str,
) -> AgentTrace:
    status = "ran" if result.status == "ok" else "failed"
    return AgentTrace(
        role_id=role.role_id,
        status=status,  # type: ignore[arg-type]
        input_artifacts=list(role.input_artifacts),
        output_path=str(output_path),
        output_summary=role_output_summary(role.role_id, payload),
        model=model,
        detail=result.detail,
        error=None if status == "ran" else result.detail,
        metadata={"retries": result.retries, "runtime_seconds": round(result.runtime_seconds, 3)},
    )


def write_role_agent_result(
    output_path: Path,
    role: RoleDefinition,
    case_id: str,
    result: AgentRunResult,
) -> dict[str, Any]:
    if result.data is None:
        payload = role_failure_payload(role.role_id, case_id, result.detail)
    else:
        payload = dict(result.data)
        payload.setdefault("schema_version", "1.0")
        payload.setdefault("role_id", role.role_id)
        payload.setdefault("case_id", case_id)
        payload["status"] = "ran"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def role_failure_payload(role_id: str, case_id: str, detail: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "1.0",
        "role_id": role_id,
        "case_id": case_id,
        "status": "failed",
        "detail": detail,
    }
    if role_id == "claim_extractor":
        base.update({"claims": [], "limitations": [detail]})
    elif role_id == "source_data_auditor":
        base.update(
            {
                "claim_to_source_data": [],
                "finding_reviews": [],
                "manual_review_tasks": [],
                "limitations": [detail],
            }
        )
    elif role_id == "judge":
        base.update(
            {
                "summary": {},
                "risk_suggestions": [],
                "report_notes": [],
                "limitations": [detail],
            }
        )
    return base


def role_output_summary(role_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if role_id == "claim_extractor":
        return {
            "claims": len(payload.get("claims") or []),
            "limitations": len(payload.get("limitations") or []),
        }
    if role_id == "source_data_auditor":
        return {
            "claim_to_source_data": len(payload.get("claim_to_source_data") or []),
            "finding_reviews": len(payload.get("finding_reviews") or []),
            "manual_review_tasks": len(payload.get("manual_review_tasks") or []),
        }
    if role_id == "judge":
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        return {
            **summary,
            "risk_suggestions": len(payload.get("risk_suggestions") or []),
            "report_notes": len(payload.get("report_notes") or []),
        }
    return {}


def write_reserved_role_output(workdir: Path, role: RoleDefinition, trace: AgentTrace) -> None:
    output_path = workdir / role.output_artifact
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "role_id": role.role_id,
        "status": trace.status,
        "detail": trace.detail,
        "input_artifacts": list(role.input_artifacts),
        "reserved_for": "static_audit_protocol.v1",
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_role_trace(workdir: Path, trace: AgentTrace) -> None:
    role_dir = workdir / "agent_traces"
    role_dir.mkdir(parents=True, exist_ok=True)
    path = role_dir / f"{trace.role_id}.json"
    path.write_text(json.dumps(asdict(trace), ensure_ascii=False, indent=2), encoding="utf-8")


def read_agent_trace(path: Path) -> AgentTrace | None:
    data = read_json(path)
    if not data:
        return None
    return AgentTrace(
        role_id=str(data.get("role_id", "")),
        status=str(data.get("status", "failed")),  # type: ignore[arg-type]
        input_artifacts=[str(item) for item in (data.get("input_artifacts") or [])],
        output_path=data.get("output_path"),
        output_summary=data.get("output_summary") if isinstance(data.get("output_summary"), dict) else {},
        model=data.get("model"),
        detail=str(data.get("detail", "")),
        error=data.get("error"),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )


def _run_static_audit_from_args(
    args: argparse.Namespace,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    paper_dir = Path(args.paper_dir).expanduser().resolve()
    if not paper_dir.is_dir():
        raise NotADirectoryError(paper_dir)

    case_id = args.case_id or paper_dir.name
    output_root = (PROJECT_ROOT / args.output_root).resolve() if not Path(args.output_root).is_absolute() else Path(args.output_root)
    workdir = output_root / case_id / "research-integrity-audit"
    if args.fresh:
        safe_remove_workdir(workdir, output_root)
    workdir.mkdir(parents=True, exist_ok=True)

    paper_pdf = discover_pdf(paper_dir)
    env = load_env(not args.no_env_file)

    steps: list[StepResult] = []
    emit_progress(
        progress,
        "audit_start",
        case_id=case_id,
        paper_dir=str(paper_dir),
        workdir=str(workdir),
        agent_mode=args.agent_mode,
    )
    record_step(
        steps,
        StepResult(
            "discover",
            "发现输入材料",
            "ran",
            f"PDF={paper_pdf}; optional data lanes will be selected from material_inventory.json",
        ),
        progress,
    )

    material_inventory_path = workdir / "material_inventory.json"
    if material_inventory_path.exists() and not args.force:
        material_inventory = read_json(material_inventory_path) or {}
        record_step(
            steps,
            StepResult("material_inventory", "材料清单扫描", "reused", "Existing material_inventory.json found."),
            progress,
        )
    else:
        material_inventory = build_material_inventory(paper_dir, paper_pdf)
        write_material_inventory(material_inventory_path, material_inventory)
        record_step(steps, StepResult("material_inventory", "材料清单扫描", "ran", str(material_inventory_path)), progress)

    agent_manifest: dict[str, Any] = {
        "mode": args.agent_mode,
        "model": args.agent_model,
        "opencode_bin": args.opencode_bin,
        "tool_registry": "paper_static_audit.v1",
        "registered_tool_ids": list(STATIC_AUDIT_V1_TOOL_IDS),
        "agent_plan_tool_ids": list(PAPER_STATIC_AUDIT_TOOL_IDS),
        "material_inventory": str(material_inventory_path),
    }

    agent_material_plan_path = workdir / "agent_material_plan.json"
    if agent_material_plan_path.exists() and not args.force:
        material_plan = read_json(agent_material_plan_path) or material_plan_from_inventory(
            case_id=case_id,
            inventory=material_inventory,
            status="fallback",
            detail="Existing agent_material_plan.json could not be parsed; deterministic fallback used.",
        )
        agent_manifest["material_plan"] = {
            "status": "reused",
            "detail": "Existing agent_material_plan.json found.",
            "runtime_seconds": None,
            "retries": 0,
            "command": [],
            "output": str(agent_material_plan_path),
        }
        record_step(
            steps,
            StepResult(
                "agent_material_plan",
                "opencode Agent 材料计划",
                "reused",
                "Existing agent_material_plan.json found.",
            ),
            progress,
        )
    elif args.agent_mode == "off":
        material_plan = material_plan_from_inventory(
            case_id=case_id,
            inventory=material_inventory,
            status="deterministic_fallback",
            detail="agent_mode=off; optional lanes were selected from deterministic material inventory only.",
        )
        agent_material_plan_path.write_text(json.dumps(material_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        agent_manifest["material_plan"] = {
            "status": "not_run",
            "detail": material_plan["detail"],
            "runtime_seconds": None,
            "retries": 0,
            "command": [],
            "output": str(agent_material_plan_path),
        }
        record_step(
            steps,
            StepResult("agent_material_plan", "opencode Agent 材料计划", "skipped", material_plan["detail"]),
            progress,
        )
    else:
        emit_step_start(
            progress,
            "agent_material_plan",
            "opencode Agent 材料计划",
            "Calling opencode to select optional material lanes.",
        )
        agent_material_plan_result = run_agent_material_plan(
            case_id=case_id,
            workdir=workdir,
            project_root=PROJECT_ROOT,
            env=env,
            model=args.agent_model,
            opencode_bin=args.opencode_bin,
            timeout_seconds=args.agent_timeout_seconds,
            max_retries=args.agent_max_retries,
        )
        if agent_material_plan_result.data:
            material_plan = agent_material_plan_result.data
            write_agent_result(agent_material_plan_path, agent_material_plan_result, "material_plan")
        else:
            material_plan = material_plan_from_inventory(
                case_id=case_id,
                inventory=material_inventory,
                status="fallback",
                detail=f"Agent material plan failed; deterministic fallback used. {agent_material_plan_result.detail}",
            )
            agent_material_plan_path.write_text(json.dumps(material_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        agent_manifest["material_plan"] = result_metadata(agent_material_plan_result, agent_material_plan_path)
        record_step(
            steps,
            StepResult(
                "agent_material_plan",
                "opencode Agent 材料计划",
                agent_step_status(agent_material_plan_result.status),
                agent_material_plan_result.detail,
                agent_material_plan_result.command,
            ),
            progress,
        )

    optional_lanes = optional_lanes_from_material_plan(material_plan, material_inventory)
    source_lane = selected_xlsx_source_lane(optional_lanes)
    source_data_dir = resolve_selected_source_root(source_lane, paper_dir)
    agent_manifest["optional_lanes"] = optional_lanes
    agent_manifest["selected_source_data_dir"] = str(source_data_dir) if source_data_dir else None

    source_finding_params = dict(DEFAULT_SOURCE_FINDING_PARAMS)
    source_finding_params.update(source_finding_params_from_lane(source_lane))
    if args.agent_mode in {"plan", "full"}:
        agent_plan_path = workdir / "agent_audit_plan.json"
        emit_step_start(
            progress,
            "agent_plan",
            "opencode Agent 审查计划",
            "Calling opencode to fill deterministic tool parameters.",
        )
        agent_plan_result = run_agent_plan(
            case_id=case_id,
            paper_pdf=paper_pdf,
            source_data_dir=source_data_dir,
            workdir=workdir,
            project_root=PROJECT_ROOT,
            env=env,
            model=args.agent_model,
            opencode_bin=args.opencode_bin,
            timeout_seconds=args.agent_timeout_seconds,
            max_retries=args.agent_max_retries,
        )
        write_agent_result(agent_plan_path, agent_plan_result, "audit_plan")
        agent_manifest["plan"] = result_metadata(agent_plan_result, agent_plan_path)
        agent_manifest["selected_tool_ids"] = selected_tool_ids_from_plan(agent_plan_result.data)
        record_step(
            steps,
            StepResult(
                "agent_plan",
                "opencode Agent 审查计划",
                agent_step_status(agent_plan_result.status),
                agent_plan_result.detail,
                agent_plan_result.command,
            ),
            progress,
        )
        if agent_plan_result.data:
            source_finding_params = source_finding_params_from_plan(agent_plan_result.data)
    else:
        agent_manifest["plan"] = None
        agent_manifest["selected_tool_ids"] = list(PAPER_STATIC_AUDIT_TOOL_IDS)

    mineru_outputs = [workdir / "full.md", workdir / "mineru_manifest.json", workdir / "images"]
    if exists_all(mineru_outputs) and not args.force:
        record_step(steps, StepResult("mineru", "MinerU PDF 解析", "reused", "Existing MinerU outputs found."), progress)
    elif not env.get("MINERU_API_TOKEN"):
        record_step(
            steps,
            StepResult("mineru", "MinerU PDF 解析", "skipped", "MINERU_API_TOKEN is missing; cannot run MinerU from scratch."),
            progress,
        )
    else:
        steps.append(
            run_command(
                "mineru",
                "MinerU PDF 解析",
                [sys.executable, "scripts/mineru_convert.py", str(paper_pdf), "--output", str(workdir)],
                mineru_outputs,
                cwd=AUDITOR_ROOT,
                env=env,
                force=args.force,
                attempts=3,
                retry_delay_seconds=10,
                progress=progress,
                stream_output=True,
            )
        )

    if (workdir / "full.md").exists():
        steps.append(
            run_command(
                "evidence_ledger",
                "构建 evidence ledger",
                [
                    sys.executable,
                    "scripts/build_evidence_ledger.py",
                    str(workdir),
                    "--output",
                    str(workdir / "evidence_ledger.json"),
                ],
                [workdir / "evidence_ledger.json"],
                cwd=AUDITOR_ROOT,
                env=env,
                force=args.force,
                progress=progress,
            )
        )
        steps.append(
            run_command(
                "numeric_forensics",
                "PDF 数字取证",
                [
                    sys.executable,
                    "scripts/numeric_forensics.py",
                    str(workdir),
                    "--output",
                    str(workdir / "numeric_forensics.json"),
                ],
                [workdir / "numeric_forensics.json"],
                cwd=AUDITOR_ROOT,
                env=env,
                force=args.force,
                progress=progress,
            )
        )
        paperfraud_output = workdir / "paperfraud_rule_matches.json"
        if paperfraud_output.exists() and not args.force:
            record_step(
                steps,
                StepResult(
                    "paperfraud_rule_match",
                    "PaperFraud 规则库匹配",
                    "reused",
                    "Existing paperfraud_rule_matches.json found.",
                ),
                progress,
            )
        else:
            emit_step_start(
                progress,
                "paperfraud_rule_match",
                "PaperFraud 规则库匹配",
                "Matching structured PaperFraud rules against parsed paper text.",
            )
            run_paperfraud_rule_match(workdir / "full.md", paperfraud_output)
            record_step(
                steps,
                StepResult("paperfraud_rule_match", "PaperFraud 规则库匹配", "ran", str(paperfraud_output)),
                progress,
            )
    else:
        record_step(steps, StepResult("evidence_ledger", "构建 evidence ledger", "skipped", "full.md missing."), progress)
        record_step(steps, StepResult("numeric_forensics", "PDF 数字取证", "skipped", "full.md missing."), progress)
        record_step(steps, StepResult("paperfraud_rule_match", "PaperFraud 规则库匹配", "skipped", "full.md missing."), progress)

    if source_lane and source_data_dir and source_data_dir.is_dir():
        steps.append(
            run_command(
                "source_data_profile",
                "Source Data profile",
                [
                    sys.executable,
                    "-m",
                    "engine.static_audit.tools.source_data_profile",
                    str(source_data_dir),
                    "--output",
                    str(workdir / "source_data_profile.json"),
                ],
                [workdir / "source_data_profile.json"],
                cwd=PROJECT_ROOT,
                env=env,
                force=args.force,
                progress=progress,
            )
        )
        if (workdir / "source_data_profile.json").exists():
            command = [
                sys.executable,
                "-m",
                "engine.static_audit.tools.source_data_findings",
                str(source_data_dir),
                "--profile",
                str(workdir / "source_data_profile.json"),
                "--output",
                str(workdir / "source_data_findings.json"),
                "--min-overlap",
                str(source_finding_params["min_overlap"]),
                "--min-support",
                str(source_finding_params["min_support"]),
                "--max-findings-per-category",
                str(source_finding_params["max_findings_per_category"]),
            ]
            if (workdir / "full.md").exists():
                command.extend(["--full-md", str(workdir / "full.md")])
            steps.append(
                run_command(
                    "source_data_findings",
                    "Source Data findings",
                    command,
                    [workdir / "source_data_findings.json"],
                    cwd=PROJECT_ROOT,
                    env=env,
                    force=args.force,
                    progress=progress,
                )
            )
            steps.append(
                run_command(
                    "source_data_pair_forensics",
                    "Source Data pair forensics",
                    [
                        sys.executable,
                        "-m",
                        "engine.static_audit.tools.source_data_pair_forensics",
                        str(source_data_dir),
                        "--output",
                        str(workdir / "source_data_pair_forensics.json"),
                    ],
                    [workdir / "source_data_pair_forensics.json"],
                    cwd=PROJECT_ROOT,
                    env=env,
                    force=args.force,
                    progress=progress,
                )
            )
        else:
            record_step(
                steps,
                StepResult("source_data_findings", "Source Data findings", "skipped", "source_data_profile.json missing."),
                progress,
            )
            record_step(
                steps,
                StepResult("source_data_pair_forensics", "Source Data pair forensics", "skipped", "source_data_profile.json missing."),
                progress,
            )
    else:
        if source_lane and source_lane.get("root"):
            source_skip_detail = f"Selected Source Data root is invalid or outside paper_dir: {source_lane.get('root')}"
        else:
            source_skip_detail = (source_lane or {}).get("reason") or "No executable XLSX/XLSM Source Data optional lane was selected."
        record_step(steps, StepResult("source_data_profile", "Source Data profile", "skipped", source_skip_detail), progress)
        record_step(steps, StepResult("source_data_findings", "Source Data findings", "skipped", source_skip_detail), progress)
        record_step(steps, StepResult("source_data_pair_forensics", "Source Data pair forensics", "skipped", source_skip_detail), progress)

    images_dir = workdir / "images"
    if images_dir.is_dir():
        steps.append(
            run_command(
                "exact_image_duplicates",
                "图片字节级重复检查",
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "exact_image_duplicates.py"),
                    str(images_dir),
                    "--output",
                    str(workdir / "exact_image_duplicates.json"),
                ],
                [workdir / "exact_image_duplicates.json"],
                cwd=PROJECT_ROOT,
                env=env,
                force=args.force,
                progress=progress,
            )
        )
        record_step(
            steps,
            StepResult(
                "image_similarity_candidates",
                "图片近似相似候选检查",
                "skipped",
                "Optional investigation tool; AgentInvestigationPlanner may select it.",
            ),
            progress,
        )
    else:
        record_step(steps, StepResult("exact_image_duplicates", "图片字节级重复检查", "skipped", "images directory missing."), progress)
        record_step(
            steps,
            StepResult("image_similarity_candidates", "图片近似相似候选检查", "skipped", "images directory missing."),
            progress,
        )

    investigation_steps, investigation_manifest = run_investigation_rounds(
        case_id=case_id,
        workdir=workdir,
        source_data_dir=source_data_dir,
        agent_enabled=args.agent_mode != "off",
        agent_mode=args.agent_mode,
        force=args.force,
        project_root=PROJECT_ROOT,
        env=env,
        model=args.agent_model,
        opencode_bin=args.opencode_bin,
        timeout_seconds=args.agent_timeout_seconds,
        max_retries=args.agent_max_retries,
        progress=progress,
    )
    steps.extend(investigation_steps)
    agent_manifest["investigation"] = investigation_manifest

    if (workdir / "vlm_triage_selected.json").exists():
        record_step(steps, StepResult("vlm_triage", "VLM 抽样初筛", "reused", "Existing VLM triage artifact found."), progress)
    else:
        record_step(
            steps,
            StepResult("vlm_triage", "VLM 抽样初筛", "skipped", "Batch VLM triage is not implemented in this orchestrator."),
            progress,
        )

    if args.agent_mode in {"review", "full"}:
        agent_review_path = workdir / "agent_review.json"
        if agent_review_path.exists() and not args.force:
            agent_manifest["review"] = {
                "status": "reused",
                "detail": "Existing agent_review.json found.",
                "runtime_seconds": None,
                "retries": 0,
                "command": [],
                "output": str(agent_review_path),
            }
            record_step(
                steps,
                StepResult(
                    "agent_review",
                    "opencode Agent 结构化审阅",
                    "reused",
                    "Existing agent_review.json found.",
                ),
                progress,
            )
        else:
            emit_step_start(
                progress,
                "agent_review",
                "opencode Agent 结构化审阅",
                "Calling opencode to review deterministic audit artifacts.",
            )
            agent_review_result = run_agent_review(
                case_id=case_id,
                workdir=workdir,
                project_root=PROJECT_ROOT,
                env=env,
                model=args.agent_model,
                opencode_bin=args.opencode_bin,
                timeout_seconds=args.agent_timeout_seconds,
                max_retries=args.agent_max_retries,
            )
            write_agent_result(agent_review_path, agent_review_result, "agent_review")
            agent_manifest["review"] = result_metadata(agent_review_result, agent_review_path)
            record_step(
                steps,
                StepResult(
                    "agent_review",
                    "opencode Agent 结构化审阅",
                    agent_step_status(agent_review_result.status),
                    agent_review_result.detail,
                    agent_review_result.command,
                ),
                progress,
            )
    else:
        agent_manifest["review"] = None

    role_steps, role_manifest = run_agent_roles(
        case_id=case_id,
        workdir=workdir,
        agent_enabled=args.agent_mode in {"review", "full"},
        agent_mode=args.agent_mode,
        force=args.force,
        project_root=PROJECT_ROOT,
        env=env,
        model=args.agent_model,
        opencode_bin=args.opencode_bin,
        timeout_seconds=args.agent_timeout_seconds,
        max_retries=args.agent_max_retries,
        progress=progress,
    )
    steps.extend(role_steps)
    agent_manifest["roles"] = role_manifest

    bundle = build_static_audit_bundle(
        paper_dir=paper_dir,
        paper_pdf=paper_pdf,
        source_data_dir=source_data_dir,
        workdir=workdir,
        case_id=case_id,
        steps=steps,
        agent_manifest=agent_manifest,
    )
    bundle_path = workdir / "static_audit_bundle.json"
    bundle.write_json(bundle_path)
    record_step(steps, StepResult("static_audit_bundle", "生成 Static Audit Bundle", "ran", str(bundle_path)), progress)

    report_path = generate_report(
        paper_dir=paper_dir,
        paper_pdf=paper_pdf,
        source_data_dir=source_data_dir,
        workdir=workdir,
        case_id=case_id,
        agent_mode=args.agent_mode,
        steps=steps,
    )
    record_step(steps, StepResult("report", "生成最终 Markdown 报告", "ran", str(report_path)), progress)

    manifest = {
        "schema_version": "1.0",
        "created_by": "engine/static_audit/orchestrator.py",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "case_id": case_id,
        "paper_dir": str(paper_dir),
        "paper_pdf": str(paper_pdf),
        "source_data_dir": str(source_data_dir) if source_data_dir else None,
        "material_inventory": str(material_inventory_path),
        "agent_material_plan": str(agent_material_plan_path),
        "optional_lanes": optional_lanes,
        "workdir": str(workdir),
        "agent": agent_manifest,
        "steps": [asdict(step) for step in steps],
        "static_audit_bundle": str(bundle_path),
        "final_report": str(report_path),
    }
    manifest_path = workdir / "audit_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    html_report_path = write_static_audit_html(workdir, case_id)
    record_step(steps, StepResult("html_report", "生成最终 HTML 报告", "ran", str(html_report_path)), progress)
    manifest["steps"] = [asdict(step) for step in steps]
    manifest["final_html_report"] = str(html_report_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "case_id": case_id,
        "workdir": str(workdir),
        "final_report": str(report_path),
        "final_html_report": str(html_report_path),
        "run_manifest": str(manifest_path),
        "static_audit_bundle": str(bundle_path),
        "failed_steps": [step.key for step in steps if step.status == "failed"],
    }
    summary["exit_code"] = 1 if any(step.status == "failed" for step in steps) else 0
    emit_progress(
        progress,
        "audit_end",
        case_id=case_id,
        status="failed" if summary["exit_code"] else "completed",
        failed_steps=summary["failed_steps"],
        final_report=str(report_path),
        final_html_report=str(html_report_path),
    )
    return summary


def run_static_audit(
    paper_dir: str | Path,
    *,
    case_id: str | None = None,
    output_root: str = "outputs",
    fresh: bool = False,
    force: bool = False,
    no_env_file: bool = False,
    agent_mode: str = "full",
    agent_model: str = "dashscope/qwen3.7-max",
    opencode_bin: str = "opencode",
    agent_timeout_seconds: int = 300,
    agent_max_retries: int = 1,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    args = argparse.Namespace(
        paper_dir=str(paper_dir),
        case_id=case_id,
        output_root=output_root,
        fresh=fresh,
        force=force,
        no_env_file=no_env_file,
        agent_mode=agent_mode,
        agent_model=agent_model,
        opencode_bin=opencode_bin,
        agent_timeout_seconds=agent_timeout_seconds,
        agent_max_retries=agent_max_retries,
    )
    return _run_static_audit_from_args(args, progress=progress)


def main() -> int:
    summary = _run_static_audit_from_args(parse_args())
    exit_code = int(summary.pop("exit_code"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
