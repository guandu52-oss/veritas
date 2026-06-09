from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SOURCE_DATA_FINDINGS_TOOL_ID = "source_data.findings"
SOURCE_DATA_PAIR_FORENSICS_TOOL_ID = "source_data.pair_forensics"
SOURCE_DATA_CROSS_SHEET_TOOL_ID = "source_data.cross_sheet"
IMAGE_SIMILARITY_TOOL_ID = "image.similarity_candidates"

EXECUTION_PHASE_MANDATORY_BOOTSTRAP = "mandatory_bootstrap"
EXECUTION_PHASE_AGENT_SELECTABLE = "agent_selectable"
EXECUTION_PHASE_RESERVED = "reserved"
EXECUTION_PHASE_REPORT_ONLY = "report_only"

SOURCE_DATA_FINDINGS_DEFAULT_PARAMS = {
    "min_overlap": 12,
    "min_support": 0.98,
    "max_findings_per_category": 200,
}


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    step_key: str
    title: str
    source: str
    description: str
    deterministic: bool = True
    expected_outputs: tuple[str, ...] = ()
    parameter_defaults: dict[str, Any] = field(default_factory=dict)
    execution_phase: str = EXECUTION_PHASE_AGENT_SELECTABLE
    agent_selectable: bool = False
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    param_schema: dict[str, dict[str, Any]] = field(default_factory=dict)


TOOLS: dict[str, ToolDefinition] = {
    "mineru.parse_pdf": ToolDefinition(
        tool_id="mineru.parse_pdf",
        step_key="mineru",
        title="MinerU PDF 解析",
        source="third_party/research-integrity-auditor",
        description="Convert the paper PDF into Markdown, images, and MinerU manifest artifacts.",
        expected_outputs=("full.md", "mineru_manifest.json", "images/"),
        execution_phase=EXECUTION_PHASE_MANDATORY_BOOTSTRAP,
        input_artifacts=("paper.pdf",),
        output_artifacts=("full.md", "mineru_manifest.json", "images/"),
    ),
    "paper.evidence_ledger": ToolDefinition(
        tool_id="paper.evidence_ledger",
        step_key="evidence_ledger",
        title="构建 evidence ledger",
        source="third_party/research-integrity-auditor",
        description="Build a structured evidence ledger from MinerU output.",
        expected_outputs=("evidence_ledger.json",),
        execution_phase=EXECUTION_PHASE_MANDATORY_BOOTSTRAP,
        input_artifacts=("full.md", "mineru_manifest.json"),
        output_artifacts=("evidence_ledger.json",),
    ),
    "paper.numeric_forensics": ToolDefinition(
        tool_id="paper.numeric_forensics",
        step_key="numeric_forensics",
        title="PDF 数字取证",
        source="third_party/research-integrity-auditor",
        description="Run deterministic numeric forensics over parsed PDF tables.",
        expected_outputs=("numeric_forensics.json",),
        execution_phase=EXECUTION_PHASE_MANDATORY_BOOTSTRAP,
        input_artifacts=("full.md",),
        output_artifacts=("numeric_forensics.json",),
    ),
    "material.inventory": ToolDefinition(
        tool_id="material.inventory",
        step_key="material_inventory",
        title="材料清单扫描",
        source="engine/static_audit",
        description="Scan submitted paper directory and classify optional evidence artifacts without interpreting them.",
        expected_outputs=("material_inventory.json",),
        execution_phase=EXECUTION_PHASE_MANDATORY_BOOTSTRAP,
        output_artifacts=("material_inventory.json",),
    ),
    "agent.material_plan": ToolDefinition(
        tool_id="agent.material_plan",
        step_key="agent_material_plan",
        title="opencode Agent 材料计划",
        source="opencode",
        description="Select optional evidence lanes from material_inventory.json using Tool Registry constraints.",
        deterministic=False,
        expected_outputs=("agent_material_plan.json",),
        execution_phase=EXECUTION_PHASE_RESERVED,
        input_artifacts=("material_inventory.json",),
        output_artifacts=("agent_material_plan.json",),
    ),
    "source_data.profile": ToolDefinition(
        tool_id="source_data.profile",
        step_key="source_data_profile",
        title="Source Data profile",
        source="veritas/scripts",
        description="Profile XLSX source data workbooks and sheets.",
        expected_outputs=("source_data_profile.json",),
        agent_selectable=True,
        input_artifacts=("agent_material_plan.json",),
        output_artifacts=("source_data_profile.json",),
    ),
    SOURCE_DATA_FINDINGS_TOOL_ID: ToolDefinition(
        tool_id=SOURCE_DATA_FINDINGS_TOOL_ID,
        step_key="source_data_findings",
        title="Source Data findings",
        source="veritas/scripts",
        description="Find duplicate columns, fixed relationships, formula-derived columns, and claim-to-source-data candidates.",
        expected_outputs=("source_data_findings.json",),
        parameter_defaults=SOURCE_DATA_FINDINGS_DEFAULT_PARAMS,
        agent_selectable=True,
        input_artifacts=("source_data_profile.json", "full.md"),
        output_artifacts=("source_data_findings.json",),
        param_schema={
            "min_overlap": {"type": "integer", "minimum": 8, "maximum": 50},
            "min_support": {"type": "number", "minimum": 0.90, "maximum": 1.0},
            "max_findings_per_category": {"type": "integer", "minimum": 20, "maximum": 500},
        },
    ),
    SOURCE_DATA_PAIR_FORENSICS_TOOL_ID: ToolDefinition(
        tool_id=SOURCE_DATA_PAIR_FORENSICS_TOOL_ID,
        step_key="source_data_pair_forensics",
        title="Source Data pair forensics",
        source="engine/static_audit/tools",
        description="Detect row-offset, paired-ratio reuse, scalar-multiple, and low-width duplicate-row patterns in XLSX Source Data.",
        expected_outputs=("source_data_pair_forensics.json",),
        parameter_defaults={
            "min_pairs": 8,
            "min_support": 0.95,
            "ratio_places": 4,
            "max_offset": 80,
            "max_findings_per_category": 50,
            "min_duplicate_row_width": 2,
        },
        agent_selectable=True,
        input_artifacts=("source_data_profile.json",),
        output_artifacts=("source_data_pair_forensics.json",),
        param_schema={
            "min_pairs": {"type": "integer", "minimum": 2, "maximum": 100},
            "min_support": {"type": "number", "minimum": 0.50, "maximum": 1.0},
            "ratio_places": {"type": "integer", "minimum": 1, "maximum": 8},
            "max_offset": {"type": "integer", "minimum": 1, "maximum": 500},
            "max_findings_per_category": {"type": "integer", "minimum": 1, "maximum": 500},
            "min_duplicate_row_width": {"type": "integer", "minimum": 2, "maximum": 20},
        },
    ),
    SOURCE_DATA_CROSS_SHEET_TOOL_ID: ToolDefinition(
        tool_id=SOURCE_DATA_CROSS_SHEET_TOOL_ID,
        step_key="source_data_cross_sheet",
        title="Source Data cross-sheet duplicates",
        source="engine/static_audit/tools",
        description="Detect duplicate numeric columns across different sheets and workbooks.",
        expected_outputs=("source_data_cross_sheet.json",),
        parameter_defaults={
            "min_overlap": 10,
            "min_support_rate": 0.8,
            "max_findings": 50,
        },
        agent_selectable=True,
        input_artifacts=("source_data_dir",),
        output_artifacts=("source_data_cross_sheet.json",),
        param_schema={
            "min_overlap": {"type": "integer", "minimum": 5, "maximum": 50},
            "min_support_rate": {"type": "number", "minimum": 0.5, "maximum": 1.0},
            "max_findings": {"type": "integer", "minimum": 10, "maximum": 200},
        },
    ),
    "image.exact_duplicates": ToolDefinition(
        tool_id="image.exact_duplicates",
        step_key="exact_image_duplicates",
        title="图片字节级重复检查",
        source="veritas/scripts",
        description="Find byte-identical extracted image files.",
        expected_outputs=("exact_image_duplicates.json",),
        execution_phase=EXECUTION_PHASE_MANDATORY_BOOTSTRAP,
        input_artifacts=("images/",),
        output_artifacts=("exact_image_duplicates.json",),
    ),
    IMAGE_SIMILARITY_TOOL_ID: ToolDefinition(
        tool_id=IMAGE_SIMILARITY_TOOL_ID,
        step_key="image_similarity_candidates",
        title="图片近似相似候选检查",
        source="engine/static_audit/tools",
        description="Find deterministic near-duplicate image candidates with dHash when Pillow is available.",
        expected_outputs=("image_similarity_candidates.json",),
        agent_selectable=True,
        input_artifacts=("images/",),
        output_artifacts=("image_similarity_candidates.json",),
        param_schema={
            "max_distance": {"type": "integer", "minimum": 0, "maximum": 32},
            "max_candidates": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
    ),
    "static_audit.bundle": ToolDefinition(
        tool_id="static_audit.bundle",
        step_key="static_audit_bundle",
        title="生成 Static Audit Bundle",
        source="engine/static_audit",
        description="Write Veritas first-party static_audit_bundle.json.",
        expected_outputs=("static_audit_bundle.json",),
        execution_phase=EXECUTION_PHASE_REPORT_ONLY,
        output_artifacts=("static_audit_bundle.json",),
    ),
    "agent.review": ToolDefinition(
        tool_id="agent.review",
        step_key="agent_review",
        title="opencode Agent 结构化审阅",
        source="opencode",
        description="Review deterministic artifacts and produce structured claim/finding review.",
        deterministic=False,
        expected_outputs=("agent_review.json",),
        execution_phase=EXECUTION_PHASE_RESERVED,
        output_artifacts=("agent_review.json",),
    ),
    "agent.role.claim_extractor": ToolDefinition(
        tool_id="agent.role.claim_extractor",
        step_key="agent_role_claim_extractor",
        title="ClaimExtractor",
        source="opencode",
        description="Extract structured technical claims from parsed paper artifacts.",
        deterministic=False,
        expected_outputs=("agent_claim_extractor.json", "agent_traces/claim_extractor.json"),
        execution_phase=EXECUTION_PHASE_RESERVED,
        output_artifacts=("agent_claim_extractor.json", "agent_traces/claim_extractor.json"),
    ),
    "agent.role.source_data_auditor": ToolDefinition(
        tool_id="agent.role.source_data_auditor",
        step_key="agent_role_source_data_auditor",
        title="SourceDataAuditor",
        source="opencode",
        description="Review Source Data findings and claim-to-source-data mappings.",
        deterministic=False,
        expected_outputs=("agent_source_data_auditor.json", "agent_traces/source_data_auditor.json"),
        execution_phase=EXECUTION_PHASE_RESERVED,
        output_artifacts=("agent_source_data_auditor.json", "agent_traces/source_data_auditor.json"),
    ),
    "agent.role.judge": ToolDefinition(
        tool_id="agent.role.judge",
        step_key="agent_role_judge",
        title="JudgeAgent",
        source="opencode",
        description="Synthesize role outputs without making a final misconduct judgment.",
        deterministic=False,
        expected_outputs=("agent_judge.json", "agent_traces/judge.json"),
        execution_phase=EXECUTION_PHASE_RESERVED,
        output_artifacts=("agent_judge.json", "agent_traces/judge.json"),
    ),
    "report.render_markdown": ToolDefinition(
        tool_id="report.render_markdown",
        step_key="report",
        title="生成最终 Markdown 报告",
        source="veritas/scripts",
        description="Render the final Markdown report and run manifest.",
        expected_outputs=("final_audit_report.md", "audit_run_manifest.json"),
        execution_phase=EXECUTION_PHASE_REPORT_ONLY,
        input_artifacts=("static_audit_bundle.json",),
        output_artifacts=("final_audit_report.md", "audit_run_manifest.json"),
    ),
    "report.render_static_html": ToolDefinition(
        tool_id="report.render_static_html",
        step_key="html_report",
        title="生成最终 HTML 报告",
        source="engine/static_audit",
        description="Render a single-file static-audit HTML demo report from structured artifacts.",
        expected_outputs=("final_audit_report.html",),
        execution_phase=EXECUTION_PHASE_REPORT_ONLY,
        input_artifacts=("audit_run_manifest.json", "static_audit_bundle.json"),
        output_artifacts=("final_audit_report.html",),
    ),
}

PAPER_STATIC_AUDIT_TOOL_IDS = (
    "mineru.parse_pdf",
    "paper.evidence_ledger",
    "paper.numeric_forensics",
    "material.inventory",
    "source_data.profile",
    SOURCE_DATA_FINDINGS_TOOL_ID,
    SOURCE_DATA_PAIR_FORENSICS_TOOL_ID,
    SOURCE_DATA_CROSS_SHEET_TOOL_ID,
    "image.exact_duplicates",
    "agent.review",
    "report.render_markdown",
)

STATIC_AUDIT_V1_TOOL_IDS = (
    "mineru.parse_pdf",
    "paper.evidence_ledger",
    "paper.numeric_forensics",
    "material.inventory",
    "agent.material_plan",
    "source_data.profile",
    SOURCE_DATA_FINDINGS_TOOL_ID,
    SOURCE_DATA_PAIR_FORENSICS_TOOL_ID,
    SOURCE_DATA_CROSS_SHEET_TOOL_ID,
    "image.exact_duplicates",
    "image.similarity_candidates",
    "agent.review",
    "agent.role.claim_extractor",
    "agent.role.source_data_auditor",
    "agent.role.judge",
    "static_audit.bundle",
    "report.render_markdown",
    "report.render_static_html",
)


def tool_catalog_for_agent() -> list[dict[str, Any]]:
    return [
        {
            "tool_id": tool.tool_id,
            "step_key": tool.step_key,
            "title": tool.title,
            "source": tool.source,
            "deterministic": tool.deterministic,
            "description": tool.description,
            "expected_outputs": list(tool.expected_outputs),
            "parameter_defaults": tool.parameter_defaults,
            "execution_phase": tool.execution_phase,
            "agent_selectable": tool.agent_selectable,
            "input_artifacts": list(tool.input_artifacts),
            "output_artifacts": list(tool.output_artifacts or tool.expected_outputs),
            "param_schema": tool.param_schema,
        }
        for tool in (TOOLS[tool_id] for tool_id in PAPER_STATIC_AUDIT_TOOL_IDS)
    ]


def tool_catalog_for_investigation() -> list[dict[str, Any]]:
    return [
        {
            "tool_id": tool.tool_id,
            "step_key": tool.step_key,
            "title": tool.title,
            "source": tool.source,
            "description": tool.description,
            "input_artifacts": list(tool.input_artifacts),
            "output_artifacts": list(tool.output_artifacts or tool.expected_outputs),
            "parameter_defaults": tool.parameter_defaults,
            "param_schema": tool.param_schema,
            "expected_evidence_examples": [
                "material_gap",
                "figure_mapping",
                "numeric_pattern",
                "image_similarity",
                "claim_mapping",
                "source_data_pattern",
            ],
        }
        for tool in TOOLS.values()
        if tool.agent_selectable and tool.deterministic
    ]


def validate_investigation_tool_action(action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise ValueError("investigation action must be an object")
    tool_id = action.get("tool_id")
    if tool_id not in TOOLS:
        raise ValueError(f"unsupported investigation tool_id: {tool_id}")
    tool = TOOLS[tool_id]
    if not tool.agent_selectable or not tool.deterministic:
        raise ValueError(f"tool_id is not agent-selectable deterministic tool: {tool_id}")
    params = action.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"investigation action params must be an object: {tool_id}")
    normalized = {
        "action_id": str(action.get("action_id") or ""),
        "tool_id": tool_id,
        "params": coerce_tool_params(tool_id, params),
        "hypothesis": str(action.get("hypothesis") or "")[:1000],
        "depends_on_artifacts": [
            str(item)
            for item in (action.get("depends_on_artifacts") or [])
            if isinstance(item, str)
        ][:20],
        "expected_evidence_type": str(action.get("expected_evidence_type") or "")[:120],
        "stop_if_no_new_evidence": bool(action.get("stop_if_no_new_evidence", True)),
    }
    if not normalized["hypothesis"]:
        raise ValueError("investigation action requires hypothesis")
    if not normalized["depends_on_artifacts"]:
        raise ValueError("investigation action requires depends_on_artifacts")
    if not normalized["expected_evidence_type"]:
        raise ValueError("investigation action requires expected_evidence_type")
    return normalized


def coerce_tool_params(tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
    if tool_id == SOURCE_DATA_FINDINGS_TOOL_ID:
        return _coerce_source_data_findings_params(params)
    if tool_id == SOURCE_DATA_PAIR_FORENSICS_TOOL_ID:
        defaults = TOOLS[tool_id].parameter_defaults
        return {
            "min_pairs": _bounded_int(params.get("min_pairs", defaults["min_pairs"]), "min_pairs", 2, 100),
            "min_support": _bounded_float(params.get("min_support", defaults["min_support"]), "min_support", 0.50, 1.0),
            "ratio_places": _bounded_int(params.get("ratio_places", defaults["ratio_places"]), "ratio_places", 1, 8),
            "max_offset": _bounded_int(params.get("max_offset", defaults["max_offset"]), "max_offset", 1, 500),
            "max_findings_per_category": _bounded_int(
                params.get("max_findings_per_category", defaults["max_findings_per_category"]),
                "max_findings_per_category",
                1,
                500,
            ),
            "min_duplicate_row_width": _bounded_int(
                params.get("min_duplicate_row_width", defaults["min_duplicate_row_width"]),
                "min_duplicate_row_width",
                2,
                20,
            ),
        }
    if tool_id == IMAGE_SIMILARITY_TOOL_ID:
        return {
            "max_distance": _bounded_int(params.get("max_distance", 8), "max_distance", 0, 32),
            "max_candidates": _bounded_int(params.get("max_candidates", 200), "max_candidates", 1, 1000),
        }
    if tool_id == SOURCE_DATA_CROSS_SHEET_TOOL_ID:
        defaults = TOOLS[tool_id].parameter_defaults
        return {
            "min_overlap": _bounded_int(params.get("min_overlap", defaults["min_overlap"]), "min_overlap", 5, 50),
            "min_support_rate": _bounded_float(params.get("min_support_rate", defaults["min_support_rate"]), "min_support_rate", 0.5, 1.0),
            "max_findings": _bounded_int(params.get("max_findings", defaults["max_findings"]), "max_findings", 10, 200),
        }
    if tool_id == "source_data.profile":
        return {}
    return dict(params)


def tool_ids_to_step_keys(tool_ids: list[str]) -> list[str]:
    return [TOOLS[tool_id].step_key for tool_id in tool_ids if tool_id in TOOLS]


def selected_tool_ids_from_plan(plan: dict[str, Any] | None) -> list[str]:
    if not plan:
        return list(PAPER_STATIC_AUDIT_TOOL_IDS)
    selected_tools = plan.get("selected_tools")
    if isinstance(selected_tools, list):
        tool_ids = [
            item.get("tool_id")
            for item in selected_tools
            if isinstance(item, dict) and item.get("tool_id") in TOOLS
        ]
        if tool_ids:
            return tool_ids
    selected_steps = plan.get("selected_steps")
    if isinstance(selected_steps, list):
        reverse = {tool.step_key: tool.tool_id for tool in TOOLS.values()}
        tool_ids = [reverse[item] for item in selected_steps if item in reverse]
        if tool_ids:
            return tool_ids
    return list(PAPER_STATIC_AUDIT_TOOL_IDS)


def source_data_findings_params_from_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(SOURCE_DATA_FINDINGS_DEFAULT_PARAMS)
    if not plan:
        return params
    selected_tools = plan.get("selected_tools")
    if isinstance(selected_tools, list):
        for item in selected_tools:
            if not isinstance(item, dict) or item.get("tool_id") != SOURCE_DATA_FINDINGS_TOOL_ID:
                continue
            tool_params = item.get("params")
            if isinstance(tool_params, dict):
                params.update(_coerce_source_data_findings_params(tool_params))
                return params
    script_parameters = plan.get("script_parameters")
    if isinstance(script_parameters, dict):
        legacy_params = script_parameters.get("source_data_findings")
        if isinstance(legacy_params, dict):
            params.update(_coerce_source_data_findings_params(legacy_params))
    return params


def validate_plan_tools(data: dict[str, Any]) -> dict[str, Any]:
    selected_tools = data.get("selected_tools")
    if selected_tools is None:
        selected_steps = data.get("selected_steps")
        if isinstance(selected_steps, list):
            reverse = {tool.step_key: tool.tool_id for tool in TOOLS.values()}
            legacy_source_params = {}
            script_parameters = data.get("script_parameters")
            if isinstance(script_parameters, dict) and isinstance(
                script_parameters.get("source_data_findings"), dict
            ):
                legacy_source_params = script_parameters["source_data_findings"]
            selected_tools = [
                {
                    "tool_id": reverse[step],
                    "params": (
                        legacy_source_params
                        if reverse[step] == SOURCE_DATA_FINDINGS_TOOL_ID
                        else {}
                    ),
                    "reason": "legacy selected_steps",
                }
                for step in selected_steps
                if step in reverse
            ]
        else:
            selected_tools = [{"tool_id": tool_id, "params": {}, "reason": "default static audit flow"} for tool_id in PAPER_STATIC_AUDIT_TOOL_IDS]
    if not isinstance(selected_tools, list):
        raise ValueError("selected_tools must be a list")

    normalized = []
    for item in selected_tools:
        if not isinstance(item, dict):
            raise ValueError("selected_tools items must be objects")
        tool_id = item.get("tool_id")
        if tool_id not in TOOLS:
            raise ValueError(f"selected_tools contains unsupported tool_id: {tool_id}")
        params = item.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"selected_tools[{tool_id}].params must be an object")
        if tool_id == SOURCE_DATA_FINDINGS_TOOL_ID:
            params = source_data_findings_params_from_plan({"selected_tools": [item]})
        normalized.append(
            {
                "tool_id": tool_id,
                "params": params,
                "reason": str(item.get("reason", ""))[:500],
            }
        )
    data["selected_tools"] = normalized
    data["selected_steps"] = tool_ids_to_step_keys([item["tool_id"] for item in normalized])
    data.setdefault("script_parameters", {})["source_data_findings"] = source_data_findings_params_from_plan(data)
    return data


def _coerce_source_data_findings_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_overlap": _bounded_int(
            params.get("min_overlap", SOURCE_DATA_FINDINGS_DEFAULT_PARAMS["min_overlap"]),
            "min_overlap",
            8,
            50,
        ),
        "min_support": _bounded_float(
            params.get("min_support", SOURCE_DATA_FINDINGS_DEFAULT_PARAMS["min_support"]),
            "min_support",
            0.90,
            1.0,
        ),
        "max_findings_per_category": _bounded_int(
            params.get(
                "max_findings_per_category",
                SOURCE_DATA_FINDINGS_DEFAULT_PARAMS["max_findings_per_category"],
            ),
            "max_findings_per_category",
            20,
            500,
        ),
    }


def _bounded_int(value: Any, key: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return parsed


def _bounded_float(value: Any, key: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a float") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return parsed
