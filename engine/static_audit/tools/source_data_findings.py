#!/usr/bin/env python3
"""Generate higher-level findings from XLSX source-data profiles and workbooks."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
import xml.etree.ElementTree as ET

from engine.static_audit.tools.source_data_profile import (
    SHEET_NS,
    normalized_number,
    parse_cell,
    read_xml,
    shared_strings,
    workbook_sheets,
)


getcontext().prec = 28


FIGURE_RE = re.compile(
    r"(Extended Data Fig\.?\s*\d+[a-z]?|Fig\.?\s*\d+[a-z]?)",
    re.IGNORECASE,
)
FORMULA_REF_RE = re.compile(r"\$?([A-Z]+)\$?(\d+)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


@dataclass
class SheetVectors:
    workbook: str
    workbook_path: str
    sheet: str
    sheet_path: str
    numeric_columns: dict[int, dict[int, Decimal]]
    text_columns: dict[int, list[tuple[int, str]]]
    formulas_by_column: dict[int, list[dict]]
    cell_count: int
    numeric_cell_count: int


def col_to_name(index: int) -> str:
    name = ""
    value = index
    while value:
        value, rem = divmod(value - 1, 26)
        name = chr(65 + rem) + name
    return name


def col_to_idx(col: str) -> int:
    value = 0
    for ch in col:
        value = value * 26 + ord(ch) - 64
    return value


def decimal_key(value: Decimal, places: str = "0.000001") -> str:
    try:
        return str(value.quantize(Decimal(places)).normalize())
    except InvalidOperation:
        return str(value.normalize())


def risk_rank(value: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}.get(value, 0)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def load_profile(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_workbook_vectors(path: Path) -> list[SheetVectors]:
    vectors = []
    with zipfile.ZipFile(path) as zf:
        shared = shared_strings(zf)
        for sheet in workbook_sheets(zf):
            root = read_xml(zf, sheet["path"])
            numeric_columns: dict[int, dict[int, Decimal]] = defaultdict(dict)
            text_columns: dict[int, list[tuple[int, str]]] = defaultdict(list)
            formulas_by_column: dict[int, list[dict]] = defaultdict(list)
            cell_count = 0
            numeric_cell_count = 0
            for cell in root.findall(".//a:sheetData/a:row/a:c", SHEET_NS):
                cell_count += 1
                parsed = parse_cell(cell, shared)
                row = parsed["row"]
                col = parsed["col"]
                if row is None or col is None:
                    continue
                if parsed["formula"]:
                    formulas_by_column[col].append(
                        {"ref": parsed["ref"], "formula": parsed["formula"]}
                    )
                if parsed["numeric"] is not None:
                    numeric_cell_count += 1
                    numeric_columns[col][row] = parsed["numeric"]
                else:
                    value = clean_text(str(parsed["value"] or ""))
                    if value:
                        text_columns[col].append((row, value))
            vectors.append(
                SheetVectors(
                    workbook=path.name,
                    workbook_path=str(path),
                    sheet=sheet["name"] or "",
                    sheet_path=sheet["path"],
                    numeric_columns=dict(numeric_columns),
                    text_columns=dict(text_columns),
                    formulas_by_column=dict(formulas_by_column),
                    cell_count=cell_count,
                    numeric_cell_count=numeric_cell_count,
                )
            )
    return vectors


def column_label(sheet: SheetVectors, col: int) -> str:
    labels = []
    for row, value in sheet.text_columns.get(col, []):
        if row <= 30 and value not in labels:
            labels.append(value)
        if len(labels) >= 4:
            break
    return " / ".join(labels)


def is_integer_like(value: Decimal) -> bool:
    return value == value.to_integral_value()


def is_index_like_column(values_by_row: dict[int, Decimal], rows: list[int]) -> bool:
    if len(rows) < 8:
        return False
    values = [values_by_row[row] for row in rows if row in values_by_row]
    if len(values) < 8:
        return False
    if sum(1 for value in values if is_integer_like(value)) / len(values) < 0.95:
        return False
    normalized = [int(value) for value in values]
    diffs = [right - left for left, right in zip(normalized, normalized[1:])]
    if not diffs:
        return False
    one_step_rate = sum(1 for diff in diffs if diff == 1) / len(diffs)
    unique_rate = len(set(normalized)) / len(normalized)
    # Source-data tables often repeat per-group subject IDs as 1..n blocks.
    # Those are index/design columns even when the whole column is not unique.
    repeated_index_blocks = one_step_rate >= 0.7
    mostly_unique_sequence = unique_rate >= 0.9 and one_step_rate >= 0.85
    return repeated_index_blocks or mostly_unique_sequence


def formula_pattern(formula: str) -> str:
    return FORMULA_REF_RE.sub(lambda match: f"{match.group(1)}<row>", formula)


def referenced_columns(formulas: list[str]) -> list[str]:
    columns = []
    for formula in formulas:
        for match in FORMULA_REF_RE.finditer(formula):
            name = match.group(1)
            if name not in columns:
                columns.append(name)
    return columns


def formula_findings(sheet: SheetVectors, limit: int) -> list[dict]:
    findings = []
    for col, formulas in sorted(sheet.formulas_by_column.items()):
        patterns = Counter(formula_pattern(item["formula"]) for item in formulas)
        top_pattern, top_count = patterns.most_common(1)[0]
        refs = referenced_columns([item["formula"] for item in formulas])
        findings.append(
            {
                "finding_id": None,
                "category": "formula_derived_column",
                "risk_level": "info",
                "confidence": "high",
                "workbook": sheet.workbook,
                "sheet": sheet.sheet,
                "target_column": col_to_name(col),
                "target_column_label": column_label(sheet, col),
                "formula_count": len(formulas),
                "dominant_formula_pattern": top_pattern,
                "dominant_formula_support": f"{top_count}/{len(formulas)}",
                "referenced_columns": refs,
                "sample_formulas": formulas[:10],
                "benign_explanations": [
                    "公式列通常是派生指标或单位换算，不应直接视为异常。",
                    "需要确认论文图表是否引用公式结果还是原始测量值。",
                ],
                "pressure_test_result": "traceability_item_not_anomaly",
                "next_steps": [
                    "将目标列映射到 figure panel 和论文 claim。",
                    "复算公式并核对图表展示值。",
                ],
            }
        )
    return findings[:limit]


def common_rows(left: dict[int, Decimal], right: dict[int, Decimal]) -> list[int]:
    return sorted(set(left).intersection(right))


def duplicate_column_findings(
    sheet: SheetVectors,
    min_overlap: int,
    min_support: float,
    limit: int,
) -> list[dict]:
    columns = sorted(
        col for col, values in sheet.numeric_columns.items() if len(values) >= min_overlap
    )
    findings = []
    for idx, left_col in enumerate(columns):
        for right_col in columns[idx + 1 :]:
            rows = common_rows(sheet.numeric_columns[left_col], sheet.numeric_columns[right_col])
            if len(rows) < min_overlap:
                continue
            equal = [
                row
                for row in rows
                if sheet.numeric_columns[left_col][row] == sheet.numeric_columns[right_col][row]
            ]
            support = len(equal) / len(rows)
            if support < min_support:
                continue
            left_label = column_label(sheet, left_col)
            right_label = column_label(sheet, right_col)
            index_like = is_index_like_column(
                sheet.numeric_columns[left_col], equal
            ) and is_index_like_column(sheet.numeric_columns[right_col], equal)
            risk = (
                "low"
                if index_like
                else ("high" if len(equal) >= 100 and left_label != right_label else "medium")
            )
            findings.append(
                {
                    "finding_id": None,
                    "category": "duplicate_numeric_columns",
                    "risk_level": risk,
                    "confidence": "high" if support == 1 else "medium",
                    "workbook": sheet.workbook,
                    "sheet": sheet.sheet,
                    "column_pair": [col_to_name(left_col), col_to_name(right_col)],
                    "column_labels": [left_label, right_label],
                    "overlap_rows": len(rows),
                    "equal_rows": len(equal),
                    "support_rate": round(support, 4),
                    "artifact_likelihood": "high" if index_like else "unknown",
                    "artifact_reason": (
                        "both columns look like repeated sequential index/subject-id columns"
                        if index_like
                        else None
                    ),
                    "sample_rows": equal[:20],
                    "sample_pairs": [
                        {
                            "row": row,
                            "left": normalized_number(sheet.numeric_columns[left_col][row]),
                            "right": normalized_number(sheet.numeric_columns[right_col][row]),
                        }
                        for row in equal[:20]
                    ],
                    "benign_explanations": [
                        "两列可能是重复展示同一指标、技术重复或空值填充结果。",
                        "如果列标签表示不同实验组或不同处理条件，则需要人工复核。",
                    ],
                    "pressure_test_result": "needs_column_semantics_review",
                    "next_steps": [
                        "核对列标题、sheet 注释和对应 figure panel。",
                        "确认是否为合法重复、派生列或数据复制。",
                    ],
                }
            )
    return sorted(findings, key=lambda item: (-item["equal_rows"], item["workbook"]))[:limit]


def fixed_relationship_findings(
    sheet: SheetVectors,
    min_overlap: int,
    min_support: float,
    limit: int,
) -> list[dict]:
    columns = sorted(
        col for col, values in sheet.numeric_columns.items() if len(values) >= min_overlap
    )
    findings = []
    formula_cols = set(sheet.formulas_by_column)
    for idx, left_col in enumerate(columns):
        for right_col in columns[idx + 1 :]:
            rows = common_rows(sheet.numeric_columns[left_col], sheet.numeric_columns[right_col])
            if len(rows) < min_overlap:
                continue
            differences = Counter()
            ratios = Counter()
            ratio_rows: dict[str, list[int]] = defaultdict(list)
            diff_rows: dict[str, list[int]] = defaultdict(list)
            for row in rows:
                left = sheet.numeric_columns[left_col][row]
                right = sheet.numeric_columns[right_col][row]
                diff_key = decimal_key(left - right)
                differences[diff_key] += 1
                diff_rows[diff_key].append(row)
                if right != 0:
                    ratio_key = decimal_key(left / right)
                    ratios[ratio_key] += 1
                    ratio_rows[ratio_key].append(row)

            diff_value, diff_count = differences.most_common(1)[0]
            if diff_value != "0" and diff_count / len(rows) >= min_support:
                findings.append(
                    relationship_record(
                        sheet,
                        "fixed_difference",
                        left_col,
                        right_col,
                        diff_value,
                        diff_count,
                        len(rows),
                        diff_rows[diff_value],
                        formula_cols,
                    )
                )

            if ratios:
                ratio_value, ratio_count = ratios.most_common(1)[0]
                if ratio_value not in {"0", "1"} and ratio_count / len(rows) >= min_support:
                    findings.append(
                        relationship_record(
                            sheet,
                            "fixed_ratio",
                            left_col,
                            right_col,
                            ratio_value,
                            ratio_count,
                            len(rows),
                            ratio_rows[ratio_value],
                            formula_cols,
                        )
                    )
    return sorted(findings, key=lambda item: (-item["support_rows"], item["workbook"]))[:limit]


def relationship_record(
    sheet: SheetVectors,
    relationship: str,
    left_col: int,
    right_col: int,
    value: str,
    support_rows: int,
    overlap_rows: int,
    rows: list[int],
    formula_cols: set[int],
) -> dict:
    formula_involved = left_col in formula_cols or right_col in formula_cols
    index_like = is_index_like_column(
        sheet.numeric_columns[left_col], rows
    ) and is_index_like_column(sheet.numeric_columns[right_col], rows)
    risk = "low" if formula_involved else ("high" if support_rows >= 100 else "medium")
    if index_like:
        risk = "low"
    return {
        "finding_id": None,
        "category": relationship,
        "risk_level": risk,
        "confidence": "high",
        "workbook": sheet.workbook,
        "sheet": sheet.sheet,
        "column_pair": [col_to_name(left_col), col_to_name(right_col)],
        "column_labels": [column_label(sheet, left_col), column_label(sheet, right_col)],
        "relationship_value": value,
        "overlap_rows": overlap_rows,
        "support_rows": support_rows,
        "support_rate": round(support_rows / overlap_rows, 4),
        "artifact_likelihood": (
            "high" if index_like else ("medium" if formula_involved else "unknown")
        ),
        "artifact_reason": (
            "both columns look like sequential index/subject-id columns with a constant offset"
            if index_like
            else ("formula column involved" if formula_involved else None)
        ),
        "sample_rows": rows[:20],
        "sample_pairs": [
            {
                "row": row,
                "left": normalized_number(sheet.numeric_columns[left_col][row]),
                "right": normalized_number(sheet.numeric_columns[right_col][row]),
            }
            for row in rows[:20]
        ],
        "formula_column_involved": formula_involved,
        "benign_explanations": [
            "可能是公式派生列、单位换算、归一化、体积/面积计算或设计矩阵编码。",
            "若列代表独立实验组或原始测量值，则机械固定关系需要人工复核。",
        ],
        "pressure_test_result": (
            "likely_index_or_design_artifact"
            if index_like
            else "likely_formula_or_transform_if_column_semantics_confirmed"
            if formula_involved
            else "needs_semantics_and_formula_review"
        ),
        "next_steps": [
            "检查该列是否有公式或是否为派生指标。",
            "核对列标题和对应论文 figure panel。",
            "确认固定关系是否覆盖原始测量值而非派生列。",
        ],
    }


def figure_keys_from_sheet_name(sheet_name: str) -> list[dict]:
    text = sheet_name.lower().replace("source data", "").strip()
    keys = []
    ed_match = re.search(r"ed\s*fig\.?\s*(\d+)([a-z]?)", text, re.IGNORECASE)
    fig_match = re.search(r"fig\.?\s*(\d+)([a-z]?)", text, re.IGNORECASE)
    if ed_match:
        figure = ed_match.group(1)
        panel = ed_match.group(2) or None
        keys.append(
            {
                "kind": "extended_data",
                "figure": figure,
                "panel": panel,
                "figure_id": f"Extended Data Fig.{figure}{panel or ''}",
                "display_label": f"Extended Data Fig. {figure}{panel or ''}",
                "patterns": [
                    rf"Extended Data Fig\.?\s*{figure}{panel or ''}\b",
                    rf"Extended Data Fig\.\s*{figure}\b",
                ],
            }
        )
    elif fig_match:
        figure = fig_match.group(1)
        panel = fig_match.group(2) or None
        keys.append(
            {
                "kind": "main_figure",
                "figure": figure,
                "panel": panel,
                "figure_id": f"Fig.{figure}{panel or ''}",
                "display_label": f"Fig. {figure}{panel or ''}",
                "patterns": [
                    rf"(?<!Extended Data )Fig\.?\s*{figure}{panel or ''}\b",
                    rf"(?<!Extended Data )Fig\.\s*{figure}\b",
                ],
            }
        )
    # Handle sheet names like "Fig.3c and 3d".
    for extra_panel in re.findall(r"\band\s*(\d+)([a-z])", text, re.IGNORECASE):
        figure, panel = extra_panel
        keys.append(
            {
                "kind": "main_figure",
                "figure": figure,
                "panel": panel,
                "figure_id": f"Fig.{figure}{panel}",
                "display_label": f"Fig. {figure}{panel}",
                "patterns": [
                    rf"(?<!Extended Data )Fig\.?\s*{figure}{panel}\b",
                    rf"(?<!Extended Data )Fig\.\s*{figure}\b",
                ],
            }
        )
    return keys


def markdown_blocks(full_md: Path) -> list[dict]:
    if not full_md or not full_md.exists():
        return []
    blocks = []
    current = []
    start = 1
    for idx, line in enumerate(full_md.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            if not current:
                start = idx
            current.append(line.strip())
            continue
        if current:
            blocks.append({"line_start": start, "line_end": idx - 1, "text": clean_text(" ".join(current))})
            current = []
    if current:
        blocks.append({"line_start": start, "line_end": start + len(current) - 1, "text": clean_text(" ".join(current))})
    return blocks


def candidate_claims_from_refs(refs: list[dict], max_claims: int = 5) -> list[dict]:
    claims = []
    seen = set()
    for ref in refs:
        text = ref["text"]
        if "|" in text:
            text = text.split("|", 1)[1].strip()
        for sentence in SENTENCE_SPLIT_RE.split(text):
            sentence = clean_text(sentence)
            if len(sentence) < 30:
                continue
            # Keep caption titles and panel-level descriptions; drop pure image markers.
            if sentence.startswith("!") or sentence.startswith("x !"):
                continue
            key = sentence[:180].lower()
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                {
                    "source": "paper_figure_reference",
                    "line_start": ref["line_start"],
                    "line_end": ref["line_end"],
                    "text": sentence[:700],
                }
            )
            if len(claims) >= max_claims:
                return claims
    return claims


def finding_index(findings: list[dict]) -> dict[tuple[str, str], list[dict]]:
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for finding in findings:
        index[(finding["workbook"], finding["sheet"])].append(
            {
                "finding_id": finding["finding_id"],
                "category": finding["category"],
                "risk_level": finding["risk_level"],
                "artifact_likelihood": finding.get("artifact_likelihood"),
                "summary": {
                    "column_pair": finding.get("column_pair"),
                    "column_labels": finding.get("column_labels"),
                    "relationship_value": finding.get("relationship_value"),
                    "support_rows": finding.get("support_rows"),
                    "equal_rows": finding.get("equal_rows"),
                    "support_rate": finding.get("support_rate"),
                },
            }
        )
    return index


def claim_mappings(
    profile: dict, full_md: Path, max_refs: int, findings: list[dict]
) -> list[dict]:
    blocks = markdown_blocks(full_md)
    mappings = []
    by_source = finding_index(findings)
    for workbook in profile.get("workbooks", []):
        for sheet in workbook.get("sheets", []):
            keys = figure_keys_from_sheet_name(sheet.get("name", ""))
            refs = []
            seen_refs = set()
            for key in keys:
                compiled = [re.compile(pattern, re.IGNORECASE) for pattern in key["patterns"]]
                for block in blocks:
                    if any(pattern.search(block["text"]) for pattern in compiled):
                        ref_key = (block["line_start"], block["line_end"], block["text"][:200])
                        if ref_key in seen_refs:
                            continue
                        seen_refs.add(ref_key)
                        refs.append(
                            {
                                "line_start": block["line_start"],
                                "line_end": block["line_end"],
                                "match_label": key["display_label"],
                                "text": block["text"][:900],
                            }
                        )
                    if len(refs) >= max_refs:
                        break
                if refs:
                    # A sheet can intentionally map to multiple panels, for example Fig.3c and 3d.
                    continue
            linked = by_source.get((workbook.get("file_name"), sheet.get("name")), [])
            priority_linked = [
                item
                for item in linked
                if risk_rank(item["risk_level"]) >= 2 and item.get("artifact_likelihood") != "high"
            ]
            mapping_confidence = "high" if refs and keys else ("medium" if refs else "low")
            mappings.append(
                {
                    "mapping_id": None,
                    "workbook": workbook.get("file_name"),
                    "sheet": sheet.get("name"),
                    "source_figure_id": ", ".join(key["figure_id"] for key in keys) or None,
                    "source_figure_kind": keys[0]["kind"] if keys else None,
                    "source_panels": [key["panel"] for key in keys if key.get("panel")],
                    "figure_keys": keys,
                    "source_data_profile": {
                        "cell_count": sheet.get("cell_count"),
                        "numeric_cell_count": sheet.get("numeric_cell_count"),
                        "formula_count": sheet.get("formula_count"),
                    },
                    "matched_paper_references": refs[:max_refs],
                    "paper_refs": refs[:max_refs],
                    "candidate_claims": candidate_claims_from_refs(refs[:max_refs]),
                    "linked_source_data_findings": linked,
                    "linked_priority_findings": priority_linked,
                    "mapping_confidence": mapping_confidence,
                    "status": "candidate_mapping" if refs else "needs_manual_mapping",
                    "review_priority": (
                        "high" if priority_linked else ("medium" if linked or refs else "low")
                    ),
                    "audit_next_step": (
                        "优先人工确认该 sheet 的列语义，并将 linked_priority_findings 与论文 claim 对账。"
                        if priority_linked
                        else "人工确认 sheet 与 figure/panel 对应关系，再抽取 panel 级 claim。"
                    ),
                    "manual_review_note": (
                        "基于 Source Data sheet 名称和论文 figure 引用的启发式映射，需要人工确认 panel 级对应关系。"
                    ),
                }
            )
    for idx, mapping in enumerate(mappings, start=1):
        mapping["mapping_id"] = f"CM-{idx:04d}"
    return mappings


def assign_ids(findings: list[dict]) -> None:
    counters: Counter[str] = Counter()
    prefix = {
        "fixed_difference": "FD",
        "fixed_ratio": "FR",
        "duplicate_numeric_columns": "DC",
        "formula_derived_column": "FM",
    }
    for finding in findings:
        category = finding["category"]
        counters[category] += 1
        finding["finding_id"] = f"{prefix.get(category, 'F')}-{counters[category]:04d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate findings from XLSX source data.")
    parser.add_argument("xlsx_root", help="Directory containing .xlsx source data files.")
    parser.add_argument("--profile", required=True, help="source_data_profile.json path.")
    parser.add_argument("--full-md", help="MinerU full.md path for claim mapping.")
    parser.add_argument("--output", required=True, help="Output source_data_findings.json path.")
    parser.add_argument("--min-overlap", type=int, default=12)
    parser.add_argument("--min-support", type=float, default=0.98)
    parser.add_argument("--max-findings-per-category", type=int, default=200)
    parser.add_argument("--max-paper-refs", type=int, default=6)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    xlsx_root = Path(args.xlsx_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    profile_path = Path(args.profile).expanduser().resolve()
    full_md = Path(args.full_md).expanduser().resolve() if args.full_md else None
    profile = load_profile(profile_path)

    duplicate_columns = []
    fixed_relationships = []
    formulas = []
    errors = []
    for workbook_path in sorted(xlsx_root.glob("*.xlsx")):
        try:
            sheets = parse_workbook_vectors(workbook_path)
        except Exception as exc:
            errors.append({"workbook": workbook_path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for sheet in sheets:
            duplicate_columns.extend(
                duplicate_column_findings(
                    sheet, args.min_overlap, args.min_support, args.max_findings_per_category
                )
            )
            fixed_relationships.extend(
                fixed_relationship_findings(
                    sheet, args.min_overlap, args.min_support, args.max_findings_per_category
                )
            )
            formulas.extend(formula_findings(sheet, args.max_findings_per_category))

    duplicate_columns = sorted(
        duplicate_columns, key=lambda item: (-item["equal_rows"], item["workbook"])
    )[: args.max_findings_per_category]
    fixed_relationships = sorted(
        fixed_relationships, key=lambda item: (-item["support_rows"], item["workbook"])
    )[: args.max_findings_per_category]
    formulas = sorted(formulas, key=lambda item: (-item["formula_count"], item["workbook"]))[
        : args.max_findings_per_category
    ]
    findings = [*duplicate_columns, *fixed_relationships, *formulas]
    assign_ids(findings)
    priority_findings = [
        finding
        for finding in findings
        if risk_rank(finding.get("risk_level", "")) >= 2
        and finding.get("artifact_likelihood") != "high"
    ]

    mappings = claim_mappings(profile, full_md, args.max_paper_refs, findings) if full_md else []
    result = {
        "schema_version": "1.1",
        "created_by": "engine/static_audit/tools/source_data_findings.py",
        "inputs": {
            "xlsx_root": str(xlsx_root),
            "profile": str(profile_path),
            "full_md": str(full_md) if full_md else None,
        },
        "parameters": {
            "min_overlap": args.min_overlap,
            "min_support": args.min_support,
            "max_findings_per_category": args.max_findings_per_category,
        },
        "summary": {
            "workbook_count": profile.get("summary", {}).get("workbook_count"),
            "sheet_count": profile.get("summary", {}).get("sheet_count"),
            "duplicate_column_findings": len(duplicate_columns),
            "fixed_relationship_findings": len(fixed_relationships),
            "formula_derived_columns": len(formulas),
            "priority_findings": len(priority_findings),
            "claim_to_source_data_mappings": len(mappings),
            "errors": len(errors),
        },
        "findings": findings,
        "priority_findings": priority_findings,
        "duplicate_columns": duplicate_columns,
        "fixed_relationships": fixed_relationships,
        "formula_derived_columns": formulas,
        "claim_to_source_data": mappings,
        "errors": errors,
        "limitations": [
            "列标签来自 XLSX 顶部文本的启发式提取，可能无法准确表达多层表头。",
            "固定差/固定比仅说明机械关系候选，需排除公式列、单位换算、设计矩阵和合法派生指标。",
            "claim-to-source-data 映射基于 sheet 名称和论文 figure 引用，尚未达到 panel 级强确认。",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
