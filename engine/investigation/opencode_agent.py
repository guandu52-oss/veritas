from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.tools.registry import (
    PAPER_STATIC_AUDIT_TOOL_IDS,
    SOURCE_DATA_FINDINGS_DEFAULT_PARAMS,
    tool_catalog_for_investigation,
    tool_catalog_for_agent,
    validate_investigation_tool_action,
    validate_plan_tools,
)

DEFAULT_SOURCE_FINDING_PARAMS = SOURCE_DATA_FINDINGS_DEFAULT_PARAMS

ALLOWED_STEPS = {
    "mineru",
    "evidence_ledger",
    "numeric_forensics",
    "material_inventory",
    "agent_material_plan",
    "source_data_profile",
    "source_data_findings",
    "source_data_pair_forensics",
    "exact_image_duplicates",
    "image_similarity_candidates",
    "agent_review",
    "static_audit_bundle",
    "report",
}

REAL_STATIC_AUDIT_ROLE_IDS = {
    "claim_extractor",
    "source_data_auditor",
    "judge",
}


@dataclass
class AgentRunResult:
    status: str
    data: dict[str, Any] | None
    detail: str
    command: list[str]
    runtime_seconds: float
    retries: int = 0


def fake_plan(
    *,
    case_id: str,
    paper_pdf: Path,
    source_data_dir: Path | None,
    workdir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "material_inventory": {
            "paper_pdf": str(paper_pdf),
            "source_data_dir": str(source_data_dir) if source_data_dir else None,
            "workdir": str(workdir),
            "code_repo_dir": None,
        },
        "selected_steps": [
            "mineru",
            "evidence_ledger",
            "numeric_forensics",
            "source_data_profile",
            "source_data_findings",
            "exact_image_duplicates",
            "agent_review",
            "report",
        ],
        "selected_tools": [
            {
                "tool_id": tool_id,
                "params": (
                    DEFAULT_SOURCE_FINDING_PARAMS
                    if tool_id == "source_data.findings"
                    else {}
                ),
                "reason": "default paper static audit flow",
            }
            for tool_id in PAPER_STATIC_AUDIT_TOOL_IDS
        ],
        "script_parameters": {
            "source_data_findings": DEFAULT_SOURCE_FINDING_PARAMS,
        },
        "missing_materials": [] if source_data_dir else ["source_data_dir"],
        "agent_rationale": [
            "Use deterministic scripts for extraction and statistics.",
            "Use Agent review after source_data_findings for claim mapping and pressure testing.",
        ],
    }


def fake_review(
    *,
    case_id: str,
    workdir: Path,
) -> dict[str, Any]:
    findings = _read_json(workdir / "source_data_findings.json") or {}
    priority = findings.get("priority_findings") or []
    mappings = findings.get("claim_to_source_data") or []
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "candidate_claims": _claims_from_mappings(mappings),
        "claim_to_source_data": _review_mappings(mappings),
        "finding_reviews": [
            {
                "finding_id": item.get("finding_id"),
                "assessment": "manual_review_required",
                "benign_explanations": item.get("benign_explanations", []),
                "residual_risk": item.get("risk_level", "medium"),
                "evidence_refs": {
                    "workbook": item.get("workbook"),
                    "sheet": item.get("sheet"),
                    "columns": item.get("column_pair"),
                },
            }
            for item in priority
        ],
        "manual_review_tasks": [
            {
                "task_id": f"MR-{idx:03d}",
                "priority": "high",
                "question": f"核对 {item.get('finding_id')} 的列语义、panel 对应关系和良性解释。",
                "evidence_refs": [item.get("workbook"), item.get("sheet")],
            }
            for idx, item in enumerate(priority, start=1)
        ],
        "report_notes": [
            "Agent review is a structured interpretation layer; deterministic artifacts remain the evidence source.",
            "Do not treat source-data candidates as misconduct conclusions.",
        ],
        "limitations": [
            "Fake Agent mode was used; no model reasoning was performed.",
        ],
    }


def fake_material_plan(*, case_id: str, workdir: Path) -> dict[str, Any]:
    inventory = _read_json(workdir / "material_inventory.json") or {}
    lanes = inventory.get("supported_optional_lanes") or []
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "selected_optional_lanes": lanes,
        "missing_materials": [] if any(item.get("status") == "selected" for item in lanes) else ["source_data_xlsx"],
        "unsupported_materials": [
            item
            for item in (inventory.get("files") or [])[:50]
            if item.get("material_type") in {"structured_table_text", "raw_data", "archive"}
        ],
        "agent_rationale": [
            "Use material_inventory.json to decide optional evidence lanes.",
            "Execute only Tool Registry validated lanes; do not run arbitrary commands.",
        ],
    }


def fake_investigation_plan(*, case_id: str, workdir: Path, round_id: int) -> dict[str, Any]:
    images_dir = workdir / "images"
    similarity_output = workdir / "image_similarity_candidates.json"
    actions = []
    if round_id == 1 and images_dir.is_dir() and not similarity_output.exists():
        actions.append(
            {
                "action_id": f"IR-{round_id:02d}-A001",
                "tool_id": "image.similarity_candidates",
                "params": {"max_distance": 8, "max_candidates": 200},
                "hypothesis": "MinerU 已抽取图片，近似重复图片候选可补充视觉人工复核线索。",
                "depends_on_artifacts": ["images/", "exact_image_duplicates.json"],
                "expected_evidence_type": "image_similarity",
                "stop_if_no_new_evidence": True,
            }
        )
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "round_id": round_id,
        "actions": actions,
        "stop_reason": "no_more_tools" if not actions else "",
        "agent_rationale": [
            "Fake planner only selects safe optional deterministic tools when required artifacts exist.",
        ],
    }


def run_agent_plan(
    *,
    case_id: str,
    paper_pdf: Path,
    source_data_dir: Path | None,
    workdir: Path,
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
) -> AgentRunResult:
    if env.get("VERITAS_FAKE_OPENCODE") == "1":
        return AgentRunResult(
            status="ok",
            data=fake_plan(
                case_id=case_id,
                paper_pdf=paper_pdf,
                source_data_dir=source_data_dir,
                workdir=workdir,
            ),
            detail="fake opencode plan",
            command=[],
            runtime_seconds=0.0,
        )
    prompt = build_plan_prompt(
        case_id=case_id,
        paper_pdf=paper_pdf,
        source_data_dir=source_data_dir,
        workdir=workdir,
    )
    return _run_opencode_json(
        prompt=prompt,
        expected="plan",
        project_root=project_root,
        env=env,
        model=model,
        opencode_bin=opencode_bin,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        validator=validate_plan,
    )


def run_agent_investigation_plan(
    *,
    case_id: str,
    workdir: Path,
    round_id: int,
    previous_records: list[dict[str, Any]],
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
) -> AgentRunResult:
    if env.get("VERITAS_FAKE_OPENCODE") == "1":
        return AgentRunResult(
            status="ok",
            data=fake_investigation_plan(case_id=case_id, workdir=workdir, round_id=round_id),
            detail=f"fake opencode investigation plan round {round_id}",
            command=[],
            runtime_seconds=0.0,
        )
    prompt = build_investigation_plan_prompt(
        case_id=case_id,
        workdir=workdir,
        round_id=round_id,
        previous_records=previous_records,
    )
    files = [
        workdir / "material_inventory.json",
        workdir / "agent_material_plan.json",
        workdir / "evidence_ledger.json",
        workdir / "numeric_forensics.json",
        workdir / "source_data_profile.json",
        workdir / "source_data_findings.json",
        workdir / "source_data_pair_forensics.json",
        workdir / "exact_image_duplicates.json",
        workdir / "image_similarity_candidates.json",
    ]
    return _run_opencode_json(
        prompt=prompt,
        expected=f"investigation plan round {round_id}",
        project_root=project_root,
        env=env,
        model=model,
        opencode_bin=opencode_bin,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        validator=lambda data: validate_investigation_plan(data, round_id=round_id),
        files=[path for path in files if path.exists()],
    )


def run_agent_material_plan(
    *,
    case_id: str,
    workdir: Path,
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
) -> AgentRunResult:
    if env.get("VERITAS_FAKE_OPENCODE") == "1":
        return AgentRunResult(
            status="ok",
            data=fake_material_plan(case_id=case_id, workdir=workdir),
            detail="fake opencode material plan",
            command=[],
            runtime_seconds=0.0,
        )
    prompt = build_material_plan_prompt(case_id=case_id, workdir=workdir)
    files = [workdir / "material_inventory.json"]
    return _run_opencode_json(
        prompt=prompt,
        expected="material plan",
        project_root=project_root,
        env=env,
        model=model,
        opencode_bin=opencode_bin,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        validator=validate_material_plan,
        files=[path for path in files if path.exists()],
    )


def run_agent_review(
    *,
    case_id: str,
    workdir: Path,
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
) -> AgentRunResult:
    if env.get("VERITAS_FAKE_OPENCODE") == "1":
        return AgentRunResult(
            status="ok",
            data=fake_review(case_id=case_id, workdir=workdir),
            detail="fake opencode review",
            command=[],
            runtime_seconds=0.0,
        )
    prompt = build_review_prompt(case_id=case_id, workdir=workdir)
    files = [
        workdir / "material_inventory.json",
        workdir / "agent_material_plan.json",
        workdir / "source_data_findings.json",
        workdir / "source_data_pair_forensics.json",
        workdir / "numeric_forensics.json",
        workdir / "source_data_profile.json",
        workdir / "exact_image_duplicates.json",
    ]
    return _run_opencode_json(
        prompt=prompt,
        expected="review",
        project_root=project_root,
        env=env,
        model=model,
        opencode_bin=opencode_bin,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        validator=validate_review,
        files=[path for path in files if path.exists()],
    )


def run_agent_role(
    *,
    role_id: str,
    case_id: str,
    workdir: Path,
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
) -> AgentRunResult:
    if role_id not in REAL_STATIC_AUDIT_ROLE_IDS:
        raise ValueError(f"unsupported real static-audit role: {role_id}")
    if env.get("VERITAS_FAKE_OPENCODE") == "1":
        return AgentRunResult(
            status="ok",
            data=fake_role_output(role_id=role_id, case_id=case_id, workdir=workdir),
            detail=f"fake opencode role {role_id}",
            command=[],
            runtime_seconds=0.0,
        )
    prompt = build_role_prompt(role_id=role_id, case_id=case_id, workdir=workdir)
    return _run_opencode_json(
        prompt=prompt,
        expected=f"role {role_id}",
        project_root=project_root,
        env=env,
        model=model,
        opencode_bin=opencode_bin,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        validator=lambda data: validate_role_output(role_id, data),
        files=_role_input_files(role_id, workdir),
    )


def build_plan_prompt(
    *,
    case_id: str,
    paper_pdf: Path,
    source_data_dir: Path | None,
    workdir: Path,
) -> str:
    tool_catalog = tool_catalog_for_agent()
    return f"""
You are Veritas Runtime Audit Agent.

Task: create a deterministic audit plan for this paper case. Do not run tools. Do not make misconduct judgments. Return ONLY one valid JSON object. The first character must be {{ and the last character must be }}. Do not wrap it in Markdown.

The research-integrity-auditor toolbox is mandatory context for this task. You are not responsible for invoking the tools directly. Veritas Python orchestrator will validate selected tool_ids against its Tool Registry and execute deterministic tools.

Case:
- case_id: {case_id}
- paper_pdf: {paper_pdf}
- source_data_dir: {source_data_dir or "missing"}
- workdir: {workdir}

Allowed Tool Registry entries:
{json.dumps(tool_catalog, ensure_ascii=False, indent=2)}

Allowed source_data_findings parameters:
- min_overlap: integer 8..50, default 12
- min_support: float 0.90..1.00, default 0.98
- max_findings_per_category: integer 20..500, default 200

Return this exact JSON shape:
{{
  "schema_version": "1.0",
  "case_id": "{case_id}",
  "material_inventory": {{
    "paper_pdf": "...",
    "source_data_dir": "... or null",
    "workdir": "...",
    "code_repo_dir": null
  }},
  "selected_tools": [
    {{
      "tool_id": "mineru.parse_pdf",
      "params": {{}},
      "reason": "..."
    }},
    {{
      "tool_id": "source_data.findings",
      "params": {{
        "min_overlap": 12,
        "min_support": 0.98,
        "max_findings_per_category": 200
      }},
      "reason": "..."
    }}
  ],
  "selected_steps": ["mineru", "evidence_ledger", "numeric_forensics", "source_data_profile", "source_data_findings", "source_data_pair_forensics", "exact_image_duplicates", "agent_review", "report"],
  "script_parameters": {{
    "source_data_findings": {{
      "min_overlap": 12,
      "min_support": 0.98,
      "max_findings_per_category": 200
    }}
  }},
  "missing_materials": [],
  "agent_rationale": ["..."]
}}
""".strip()


def build_review_prompt(*, case_id: str, workdir: Path) -> str:
    summary = _artifact_summary(workdir)
    return f"""
You are Veritas Runtime Review Agent.

Task: review deterministic audit artifacts and produce structured claim/finding review. Do not run tools. Do not modify files. Do not make final misconduct judgments. Return ONLY one valid JSON object. The first character must be {{ and the last character must be }}. Do not wrap it in Markdown.

Case:
- case_id: {case_id}
- workdir: {workdir}

Artifact summary:
{json.dumps(summary, ensure_ascii=False, indent=2)}

Focus:
- extract candidate claims from figure/source-data mappings
- review source-data priority findings
- pressure-test benign explanations
- create manual review tasks

Language:
- Use Chinese for all natural-language explanations, report_notes, limitations, review questions, benign explanations, and manual-review instructions.
- Keep professional terms and provenance evidence unchanged: claim, finding, Source Data, Agent, Tool Registry, workbook/sheet names, file paths, tool_id, artifact names, figure labels, evidence refs, code identifiers, and quoted paper claims.

Return this exact JSON shape:
{{
  "schema_version": "1.0",
  "case_id": "{case_id}",
  "candidate_claims": [
    {{
      "claim_id": "AC-001",
      "claim_text": "...",
      "claim_type": "numeric|method|figure_trace|code_execution|material_completeness",
      "paper_location": "...",
      "evidence_refs": ["..."],
      "status": "needs_review"
    }}
  ],
  "claim_to_source_data": [
    {{
      "claim_id": "AC-001",
      "mapping_id": "...",
      "source_data_refs": ["..."],
      "confidence": "low|medium|high",
      "needs_human_review": true
    }}
  ],
  "finding_reviews": [
    {{
      "finding_id": "...",
      "assessment": "manual_review_required|likely_artifact|needs_more_evidence",
      "benign_explanations": ["..."],
      "residual_risk": "low|medium|high",
      "evidence_refs": {{}}
    }}
  ],
  "manual_review_tasks": [
    {{
      "task_id": "MR-001",
      "priority": "low|medium|high",
      "question": "...",
      "evidence_refs": ["..."]
    }}
  ],
  "report_notes": ["..."],
  "limitations": ["..."]
}}
""".strip()


def build_material_plan_prompt(*, case_id: str, workdir: Path) -> str:
    inventory = _read_json(workdir / "material_inventory.json") or {}
    compact_inventory = {
        "summary": inventory.get("summary", {}),
        "candidate_source_roots": inventory.get("candidate_source_roots", [])[:12],
        "supported_optional_lanes": inventory.get("supported_optional_lanes", [])[:8],
        "limitations": inventory.get("limitations", []),
    }
    return f"""
You are Veritas Material Planner.

Task: inspect material_inventory.json and choose optional evidence lanes. PDF analysis is mandatory and already handled by deterministic code. Your job is only optional data/material lanes.

Rules:
- Do not run tools.
- Do not invent files.
- Select only Tool Registry lanes that are supported by the inventory.
- Current MVP can execute XLSX/XLSM Source Data with tool_ids source_data.profile, source_data.findings, and source_data.pair_forensics.
- CSV/TSV/raw/archive materials should be reported as unsupported_materials unless an executable lane exists.
- Use Chinese for natural-language fields such as reason, unsupported_materials.reason, and agent_rationale. Keep professional terms, tool_id, lane_id, file paths, artifact names, workbook/sheet names, and evidence refs unchanged.
- Return ONLY one valid JSON object. The first character must be {{ and the last character must be }}.

Case:
- case_id: {case_id}
- workdir: {workdir}

Compact inventory:
{json.dumps(compact_inventory, ensure_ascii=False, indent=2)}

Return this exact JSON shape:
{{
  "schema_version": "1.0",
  "case_id": "{case_id}",
  "selected_optional_lanes": [
    {{
      "lane_id": "source_data_xlsx",
      "status": "selected|missing_material|unsupported",
      "tool_ids": ["source_data.profile", "source_data.findings", "source_data.pair_forensics"],
      "root": "... or null",
      "reason": "...",
      "params": {{
        "source_data_findings": {{
          "min_overlap": 12,
          "min_support": 0.98,
          "max_findings_per_category": 200
        }}
      }}
    }}
  ],
  "missing_materials": ["..."],
  "unsupported_materials": [
    {{
      "path": "...",
      "material_type": "...",
      "reason": "..."
    }}
  ],
  "agent_rationale": ["..."]
}}
""".strip()


def build_investigation_plan_prompt(
    *,
    case_id: str,
    workdir: Path,
    round_id: int,
    previous_records: list[dict[str, Any]],
) -> str:
    summary = _artifact_summary(workdir)
    compact_records = previous_records[-30:]
    tool_catalog = tool_catalog_for_investigation()
    return f"""
You are Veritas AgentInvestigationPlanner.

Task: choose deterministic follow-up investigation tools for static paper audit round {round_id}. You do not run tools. Veritas Python orchestrator validates every action against Tool Registry and executes accepted deterministic tools.

Rules:
- Select only tool_id values from Agent-selectable Tool Registry entries.
- Do not select Agent tools such as agent.review or JudgeAgent.
- Mandatory bootstrap tools have already run or have been skipped by deterministic prerequisites; do not request mineru.parse_pdf, paper.evidence_ledger, paper.numeric_forensics, image.exact_duplicates, report tools, or Agent tools.
- Every action must include hypothesis, depends_on_artifacts, and expected_evidence_type.
- Avoid repeating any previous tool_id + params + depends_on_artifacts combination.
- Prefer no actions over noisy actions. If no useful deterministic follow-up is available, return actions=[] and stop_reason="no_more_tools".
- Use Chinese for natural-language fields. Keep tool_id, artifact names, file paths, workbook/sheet names, evidence refs, and professional terms unchanged.
- Return ONLY one valid JSON object. The first character must be {{ and the last character must be }}. Do not wrap it in Markdown.

Case:
- case_id: {case_id}
- workdir: {workdir}
- round_id: {round_id}
- max_rounds: 3

Artifact summary:
{json.dumps(summary, ensure_ascii=False, indent=2)}

Previous investigation records:
{json.dumps(compact_records, ensure_ascii=False, indent=2)}

Agent-selectable Tool Registry entries:
{json.dumps(tool_catalog, ensure_ascii=False, indent=2)}

Return this exact JSON shape:
{{
  "schema_version": "1.0",
  "case_id": "{case_id}",
  "round_id": {round_id},
  "actions": [
    {{
      "action_id": "IR-{round_id:02d}-A001",
      "tool_id": "image.similarity_candidates",
      "params": {{"max_distance": 8, "max_candidates": 200}},
      "hypothesis": "...",
      "depends_on_artifacts": ["images/", "exact_image_duplicates.json"],
      "expected_evidence_type": "image_similarity",
      "stop_if_no_new_evidence": true
    }}
  ],
  "stop_reason": "no_more_tools|budget_exhausted|waiting_for_human|",
  "agent_rationale": ["..."]
}}
""".strip()


def build_role_prompt(*, role_id: str, case_id: str, workdir: Path) -> str:
    summary = _artifact_summary(workdir)
    if role_id == "claim_extractor":
        contract = f"""
Return this exact JSON shape:
{{
  "schema_version": "1.0",
  "role_id": "claim_extractor",
  "case_id": "{case_id}",
  "claims": [
    {{
      "claim_id": "AC-001",
      "claim_text": "...",
      "claim_type": "numeric|method|figure_trace|code_execution|material_completeness",
      "paper_location": "...",
      "evidence_refs": ["..."],
      "status": "needs_review"
    }}
  ],
  "limitations": ["..."]
}}
""".strip()
        focus = "Extract only technical claims that can be checked against Source Data, figures, code, methods, or material completeness."
    elif role_id == "source_data_auditor":
        contract = f"""
Return this exact JSON shape:
{{
  "schema_version": "1.0",
  "role_id": "source_data_auditor",
  "case_id": "{case_id}",
  "claim_to_source_data": [
    {{
      "claim_id": "AC-001",
      "mapping_id": "...",
      "source_data_refs": ["..."],
      "confidence": "low|medium|high",
      "needs_human_review": true
    }}
  ],
  "finding_reviews": [
    {{
      "finding_id": "...",
      "assessment": "manual_review_required|likely_artifact|needs_more_evidence",
      "benign_explanations": ["..."],
      "residual_risk": "low|medium|high",
      "evidence_refs": {{}}
    }}
  ],
  "manual_review_tasks": [
    {{
      "task_id": "MR-001",
      "priority": "low|medium|high",
      "question": "...",
      "evidence_refs": ["..."]
    }}
  ],
  "limitations": ["..."]
}}
""".strip()
        focus = "Review deterministic Source Data findings, pressure-test benign explanations, and create manual review tasks. Limit claim mappings, finding reviews, and manual tasks to at most 12 items each; prioritize high-risk deterministic findings."
    elif role_id == "judge":
        contract = f"""
Return this exact JSON shape:
{{
  "schema_version": "1.0",
  "role_id": "judge",
  "case_id": "{case_id}",
  "summary": {{
    "claim_count": 0,
    "finding_review_count": 0,
    "manual_review_task_count": 0,
    "technical_risk_summary": "..."
  }},
  "risk_suggestions": [
    {{
      "risk_level": "low|medium|high",
      "reason": "...",
      "evidence_refs": ["..."],
      "requires_human_review": true
    }}
  ],
  "report_notes": ["..."],
  "limitations": ["..."]
}}
""".strip()
        focus = "Synthesize prior role outputs. Do not override deterministic evidence and do not make a final misconduct judgment."
    else:
        raise ValueError(f"unsupported role prompt: {role_id}")
    return f"""
You are Veritas Static Audit Role Agent: {role_id}.

Task: {focus}

Rules:
- Do not run tools.
- Do not modify files.
- Do not make final academic-value or misconduct judgments.
- Treat deterministic artifacts as evidence; your output is interpretation and review planning.
- Use Chinese for natural-language fields, including limitations, benign_explanations, manual_review_tasks.question, report_notes, risk_suggestions.reason, and technical_risk_summary. Keep professional terms and provenance evidence unchanged: claim, finding, Source Data, Agent, Tool Registry, workbook/sheet names, file paths, artifact names, figure labels, evidence refs, code identifiers, and quoted paper claims.
- Return ONLY one valid JSON object. The first character must be {{ and the last character must be }}. Do not wrap it in Markdown.

Case:
- case_id: {case_id}
- workdir: {workdir}

Artifact summary:
{json.dumps(summary, ensure_ascii=False, indent=2)}

{contract}
""".strip()


def validate_plan(data: dict[str, Any]) -> dict[str, Any]:
    _require(data, "schema_version", str)
    _require(data, "case_id", str)
    _require(data, "material_inventory", dict)
    data = validate_plan_tools(data)
    selected = _require(data, "selected_steps", list)
    for step in selected:
        if step not in ALLOWED_STEPS:
            raise ValueError(f"selected_steps contains unsupported step: {step}")
    data.setdefault("missing_materials", [])
    data.setdefault("agent_rationale", [])
    return data


def validate_review(data: dict[str, Any]) -> dict[str, Any]:
    _require(data, "schema_version", str)
    _require(data, "case_id", str)
    for key in [
        "candidate_claims",
        "claim_to_source_data",
        "finding_reviews",
        "manual_review_tasks",
        "report_notes",
        "limitations",
    ]:
        _require(data, key, list)
    return data


def validate_material_plan(data: dict[str, Any]) -> dict[str, Any]:
    _require(data, "schema_version", str)
    _require(data, "case_id", str)
    lanes = _require(data, "selected_optional_lanes", list)
    normalized = []
    for lane in lanes:
        if not isinstance(lane, dict):
            raise ValueError("selected_optional_lanes items must be objects")
        lane_id = str(lane.get("lane_id", ""))
        if lane_id != "source_data_xlsx":
            raise ValueError(f"unsupported optional lane_id: {lane_id}")
        status = str(lane.get("status", "missing_material"))
        if status not in {"selected", "missing_material", "unsupported"}:
            raise ValueError(f"unsupported optional lane status: {status}")
        tool_ids = lane.get("tool_ids") or []
        if status == "selected":
            required_tool_ids = ["source_data.profile", "source_data.findings", "source_data.pair_forensics"]
            if tool_ids != required_tool_ids:
                raise ValueError("source_data_xlsx selected lane must use source_data.profile, source_data.findings, and source_data.pair_forensics")
            if not lane.get("root"):
                raise ValueError("source_data_xlsx selected lane requires root")
        params = lane.get("params") if isinstance(lane.get("params"), dict) else {}
        source_params = params.get("source_data_findings") if isinstance(params.get("source_data_findings"), dict) else {}
        normalized.append(
            {
                "lane_id": lane_id,
                "status": status,
                "tool_ids": tool_ids if status == "selected" else [],
                "root": lane.get("root") if status == "selected" else None,
                "reason": str(lane.get("reason", ""))[:500],
                "params": {
                    "source_data_findings": _coerce_material_source_params(source_params),
                },
            }
        )
    data["selected_optional_lanes"] = normalized
    data.setdefault("missing_materials", [])
    data.setdefault("unsupported_materials", [])
    data.setdefault("agent_rationale", [])
    return data


def validate_investigation_plan(data: dict[str, Any], *, round_id: int) -> dict[str, Any]:
    _require(data, "schema_version", str)
    _require(data, "case_id", str)
    actual_round_id = _require(data, "round_id", int)
    if actual_round_id != round_id:
        raise ValueError(f"round_id must be {round_id}")
    actions = _require(data, "actions", list)
    normalized_actions = []
    for index, action in enumerate(actions, start=1):
        normalized = validate_investigation_tool_action(action)
        if not normalized["action_id"]:
            normalized["action_id"] = f"IR-{round_id:02d}-A{index:03d}"
        normalized_actions.append(normalized)
    data["actions"] = normalized_actions
    data["stop_reason"] = str(data.get("stop_reason") or "")
    data.setdefault("agent_rationale", [])
    _require(data, "agent_rationale", list)
    return data


def validate_role_output(role_id: str, data: dict[str, Any]) -> dict[str, Any]:
    _require(data, "schema_version", str)
    actual_role_id = _require(data, "role_id", str)
    if actual_role_id != role_id:
        raise ValueError(f"role_id must be {role_id}")
    _require(data, "case_id", str)
    if role_id == "claim_extractor":
        _require(data, "claims", list)
        data.setdefault("limitations", [])
        _require(data, "limitations", list)
    elif role_id == "source_data_auditor":
        for key in [
            "claim_to_source_data",
            "finding_reviews",
            "manual_review_tasks",
            "limitations",
        ]:
            _require(data, key, list)
    elif role_id == "judge":
        _require(data, "summary", dict)
        for key in ["risk_suggestions", "report_notes", "limitations"]:
            _require(data, key, list)
    else:
        raise ValueError(f"unsupported role validator: {role_id}")
    return data


def write_agent_result(path: Path, result: AgentRunResult, fallback_kind: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.data if result.data is not None else {
        "schema_version": "1.0",
        "status": "failed",
        "kind": fallback_kind,
        "detail": result.detail,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def result_metadata(result: AgentRunResult, output_path: Path) -> dict[str, Any]:
    return {
        "status": result.status,
        "detail": result.detail,
        "runtime_seconds": round(result.runtime_seconds, 3),
        "retries": result.retries,
        "command": result.command,
        "output": str(output_path),
    }


def _run_opencode_json(
    *,
    prompt: str,
    expected: str,
    project_root: Path,
    env: dict[str, str],
    model: str,
    opencode_bin: str,
    timeout_seconds: int,
    max_retries: int,
    validator: Any,
    files: list[Path] | None = None,
) -> AgentRunResult:
    command = [
        opencode_bin,
        "run",
        prompt,
        "--format",
        "json",
        "--model",
        model,
        "--dir",
        str(project_root),
    ]
    env.setdefault("XDG_DATA_HOME", str(project_root / ".opencode" / "data"))
    for path in files or []:
        command.extend(["--file", str(path)])

    last_detail = ""
    start_all = time.monotonic()
    for attempt in range(max_retries + 1):
        attempt_prompt = prompt
        if attempt and last_detail:
            attempt_prompt = (
                f"{prompt}\n\nPrevious JSON validation failed: {last_detail}\n"
                "Return corrected JSON only."
            )
            command[2] = attempt_prompt
        start = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=project_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_detail = f"opencode {expected} timed out after {timeout_seconds}s"
            continue
        except OSError as exc:
            last_detail = f"opencode launch failed: {exc}"
            break
        runtime = time.monotonic() - start
        if completed.returncode != 0:
            last_detail = f"opencode exit_code={completed.returncode} stderr_tail={completed.stderr[-1000:]!r}"
            continue
        try:
            parsed = extract_json(completed.stdout)
            data = validator(parsed)
            return AgentRunResult("ok", data, f"opencode {expected} ok", command, runtime, attempt)
        except Exception as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
            if completed.stdout:
                last_detail += f" stdout_tail={completed.stdout[-1000:]!r}"
            if completed.stderr:
                last_detail += f" stderr_tail={completed.stderr[-1000:]!r}"
    return AgentRunResult(
        "failed",
        None,
        last_detail or f"opencode {expected} failed",
        command,
        time.monotonic() - start_all,
        max_retries,
    )


def extract_json(text: str) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("empty opencode output")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("type") == "text":
            part = parsed.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return _extract_json_from_text(part["text"])
        if isinstance(parsed, dict) and "schema_version" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass

    event_texts: list[str] = []
    fallback_texts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") == "text":
            part = item.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                event_texts.append(part["text"])
                continue
        fallback_texts.extend(_collect_strings(item))
    if event_texts:
        return _extract_json_from_text("\n".join(event_texts))
    combined = "\n".join(fallback_texts) if fallback_texts else text
    return _extract_json_from_text(combined)


def _extract_json_from_text(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    for candidate in _json_object_candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found in opencode output")


def _json_object_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    starts = [idx for idx, char in enumerate(text) if char == "{"]
    for start in starts:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : idx + 1])
                    break
    return sorted(candidates, key=len, reverse=True)


def _collect_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, list):
        for item in value:
            strings.extend(_collect_strings(item))
    elif isinstance(value, dict):
        for item in value.values():
            strings.extend(_collect_strings(item))
    return strings


def _artifact_summary(workdir: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"workdir": str(workdir)}
    material_inventory = _read_json(workdir / "material_inventory.json") or {}
    material_plan = _read_json(workdir / "agent_material_plan.json") or {}
    ledger = _read_json(workdir / "evidence_ledger.json") or {}
    numeric = _read_json(workdir / "numeric_forensics.json") or {}
    source_findings = _read_json(workdir / "source_data_findings.json") or {}
    pair_forensics = _read_json(workdir / "source_data_pair_forensics.json") or {}
    image_duplicates = _read_json(workdir / "exact_image_duplicates.json") or {}
    investigation_records = _read_investigation_records(workdir)
    inventory_summary = material_inventory.get("summary") or {}
    summary["material_inventory"] = {
        "file_count": inventory_summary.get("file_count"),
        "by_material_type": inventory_summary.get("by_material_type", {}),
        "candidate_source_roots": (material_inventory.get("candidate_source_roots") or [])[:8],
        "limitations": material_inventory.get("limitations", []),
    }
    summary["material_plan"] = {
        "status": material_plan.get("status", "ok") if material_plan else "missing",
        "selected_optional_lanes": material_plan.get("selected_optional_lanes", []),
        "missing_materials": material_plan.get("missing_materials", []),
        "unsupported_materials": (material_plan.get("unsupported_materials") or [])[:12],
    }
    summary["evidence_ledger_stats"] = ledger.get("stats", {})
    summary["evidence_ledger_warnings"] = [
        {"code": item.get("code"), "message": str(item.get("message", ""))[:220]}
        for item in (ledger.get("warnings") or [])[:10]
        if isinstance(item, dict)
    ]
    benford = numeric.get("benford") or {}
    summary["numeric_forensics"] = {
        "all_number_count": numeric.get("all_number_count"),
        "number_count": numeric.get("number_count"),
        "table_count": numeric.get("table_count"),
        "effective_scope": numeric.get("effective_scope"),
        "benford_applicability": benford.get("applicability"),
        "benford_mad": benford.get("mad", benford.get("mean_absolute_deviation")),
    }
    summary["source_data_findings_summary"] = source_findings.get("summary", {})
    summary["source_data_pair_forensics_summary"] = pair_forensics.get("summary", {})
    summary["source_data_pair_forensics_priority"] = [
        _compact_pair_forensics_finding(item)
        for item in (pair_forensics.get("priority_findings") or [])[:12]
        if isinstance(item, dict)
    ]
    summary["priority_findings"] = [
        _compact_priority_finding(item)
        for item in (source_findings.get("priority_findings") or [])[:12]
        if isinstance(item, dict)
    ]
    summary["claim_to_source_data"] = [
        _compact_claim_mapping(item)
        for item in (source_findings.get("claim_to_source_data") or [])[:18]
        if isinstance(item, dict)
    ]
    summary["image_duplicates"] = {
        "image_count": image_duplicates.get("image_count"),
        "duplicate_group_count": image_duplicates.get("duplicate_group_count"),
        "duplicate_image_count": image_duplicates.get("duplicate_image_count"),
    }
    image_similarity = _read_json(workdir / "image_similarity_candidates.json") or {}
    summary["image_similarity_candidates"] = {
        "status": image_similarity.get("status"),
        "method": image_similarity.get("method"),
        "image_count": image_similarity.get("image_count"),
        "candidate_count": image_similarity.get("candidate_count"),
    }
    summary["investigation_records"] = investigation_records[-20:]
    return summary


def fake_role_output(*, role_id: str, case_id: str, workdir: Path) -> dict[str, Any]:
    review = fake_review(case_id=case_id, workdir=workdir)
    if role_id == "claim_extractor":
        return {
            "schema_version": "1.0",
            "role_id": role_id,
            "case_id": case_id,
            "claims": review["candidate_claims"],
            "limitations": review["limitations"],
        }
    if role_id == "source_data_auditor":
        return {
            "schema_version": "1.0",
            "role_id": role_id,
            "case_id": case_id,
            "claim_to_source_data": review["claim_to_source_data"],
            "finding_reviews": review["finding_reviews"],
            "manual_review_tasks": review["manual_review_tasks"],
            "limitations": review["limitations"],
        }
    if role_id == "judge":
        return {
            "schema_version": "1.0",
            "role_id": role_id,
            "case_id": case_id,
            "summary": {
                "claim_count": len(review["candidate_claims"]),
                "finding_review_count": len(review["finding_reviews"]),
                "manual_review_task_count": len(review["manual_review_tasks"]),
                "technical_risk_summary": "Structured static-audit review requires human verification before escalation.",
            },
            "risk_suggestions": [],
            "report_notes": review["report_notes"],
            "limitations": review["limitations"],
        }
    raise ValueError(f"unsupported fake role: {role_id}")


def _role_input_files(role_id: str, workdir: Path) -> list[Path]:
    candidates: dict[str, list[Path]] = {
        "claim_extractor": [
            workdir / "material_inventory.json",
            workdir / "agent_material_plan.json",
            workdir / "full.md",
            workdir / "evidence_ledger.json",
            workdir / "source_data_findings.json",
            workdir / "source_data_pair_forensics.json",
        ],
        "source_data_auditor": [
            workdir / "material_inventory.json",
            workdir / "agent_material_plan.json",
            workdir / "source_data_findings.json",
            workdir / "source_data_pair_forensics.json",
            workdir / "agent_claim_extractor.json",
        ],
        "judge": [
            workdir / "material_inventory.json",
            workdir / "agent_material_plan.json",
            workdir / "agent_claim_extractor.json",
            workdir / "agent_source_data_auditor.json",
            workdir / "numeric_forensics.json",
            workdir / "source_data_findings.json",
            workdir / "source_data_pair_forensics.json",
        ],
    }
    return [path for path in candidates.get(role_id, []) if path.exists()]


def _compact_priority_finding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": item.get("finding_id"),
        "risk_level": item.get("risk_level"),
        "category": item.get("category"),
        "workbook": item.get("workbook"),
        "sheet": item.get("sheet"),
        "column_pair": item.get("column_pair"),
        "relationship_value": item.get("relationship_value"),
        "support_rows": item.get("support_rows") or item.get("equal_rows"),
        "overlap_rows": item.get("overlap_rows"),
        "benign_explanations": (item.get("benign_explanations") or [])[:3],
    }


def _compact_pair_forensics_finding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": item.get("finding_id"),
        "risk_level": item.get("risk_level"),
        "category": item.get("category"),
        "workbook": item.get("workbook"),
        "sheet": item.get("sheet"),
        "offset": item.get("row_offset") or item.get("pair_id_offset"),
        "columns": item.get("columns") or item.get("column_pair") or item.get("column"),
        "support": item.get("support_rows")
        or item.get("matched_pairs")
        or item.get("matched_pair_groups")
        or item.get("duplicate_row_count")
        or item.get("exact_reuse_pairs"),
        "overlap": item.get("overlap_rows") or item.get("overlap_pairs") or item.get("overlap_pair_groups"),
        "support_rate": item.get("support_rate"),
        "sample_pairs": (item.get("sample_pairs") or item.get("sample_exact_pairs") or [])[:5],
    }


def _compact_claim_mapping(item: dict[str, Any]) -> dict[str, Any]:
    claims = item.get("candidate_claims") or []
    first_claim = claims[0] if claims and isinstance(claims[0], dict) else {}
    linked = item.get("linked_priority_findings") or []
    return {
        "mapping_id": item.get("mapping_id"),
        "source_figure_id": item.get("source_figure_id"),
        "workbook": item.get("workbook"),
        "sheet": item.get("sheet"),
        "review_priority": item.get("review_priority"),
        "mapping_confidence": item.get("mapping_confidence"),
        "candidate_claim": {
            "text": str(first_claim.get("text", ""))[:280],
            "location": first_claim.get("location"),
        },
        "linked_priority_findings": [
            linked_item.get("finding_id")
            for linked_item in linked[:6]
            if isinstance(linked_item, dict)
        ],
    }


def _claims_from_mappings(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for idx, mapping in enumerate(mappings[:12], start=1):
        candidate_claims = mapping.get("candidate_claims") or []
        text = candidate_claims[0].get("text") if candidate_claims else None
        if not text:
            continue
        claims.append(
            {
                "claim_id": f"AC-{idx:03d}",
                "claim_text": text,
                "claim_type": "figure_trace",
                "paper_location": mapping.get("source_figure_id"),
                "evidence_refs": [mapping.get("mapping_id"), mapping.get("sheet")],
                "status": "needs_review",
            }
        )
    return claims


def _review_mappings(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, mapping in enumerate(mappings[:12], start=1):
        rows.append(
            {
                "claim_id": f"AC-{idx:03d}",
                "mapping_id": mapping.get("mapping_id"),
                "source_data_refs": [mapping.get("workbook"), mapping.get("sheet")],
                "confidence": mapping.get("mapping_confidence", "medium"),
                "needs_human_review": mapping.get("review_priority") == "high",
            }
        )
    return rows


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_investigation_records(workdir: Path) -> list[dict[str, Any]]:
    path = workdir / "investigation_rounds.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _coerce_material_source_params(params: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(DEFAULT_SOURCE_FINDING_PARAMS)
    for key, default in DEFAULT_SOURCE_FINDING_PARAMS.items():
        value = params.get(key, default)
        try:
            coerced[key] = int(value) if isinstance(default, int) else float(value)
        except (TypeError, ValueError):
            coerced[key] = default
    coerced["min_overlap"] = max(8, min(50, int(coerced["min_overlap"])))
    coerced["min_support"] = max(0.90, min(1.0, float(coerced["min_support"])))
    coerced["max_findings_per_category"] = max(20, min(500, int(coerced["max_findings_per_category"])))
    return coerced


def _require(data: dict[str, Any], key: str, expected: type) -> Any:
    value = data.get(key)
    if not isinstance(value, expected):
        raise ValueError(f"{key} must be {expected.__name__}")
    return value
