#!/usr/bin/env python3
"""Detect row-offset and paired-cohort patterns in XLSX Source Data."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from engine.static_audit.tools.source_data_findings import (
    SheetVectors,
    col_to_name,
    column_label,
    parse_workbook_vectors,
)
from engine.static_audit.tools.source_data_profile import normalized_number


RISK_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


@dataclass(frozen=True)
class PairForensicsParams:
    min_pairs: int = 8
    min_support: float = 0.95
    ratio_places: int = 4
    max_offset: int = 80
    max_findings_per_category: int = 50
    min_duplicate_row_width: int = 2


def risk_rank(value: str) -> int:
    return RISK_ORDER.get(value, 0)


def decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def ratio_key(numerator: Decimal, denominator: Decimal, places: int) -> str | None:
    if denominator == 0:
        return None
    quant = Decimal(1).scaleb(-places)
    try:
        return str((numerator / denominator).quantize(quant).normalize())
    except (InvalidOperation, ZeroDivisionError):
        return None


def common_offset_pairs(rows: list[int], offset: int) -> list[tuple[int, int]]:
    row_set = set(rows)
    return [(row, row + offset) for row in rows if row + offset in row_set]


def numeric_value_diversity(values_by_row: dict[int, Decimal]) -> tuple[int, int, float]:
    values = [normalized_number(value) for value in values_by_row.values()]
    total = len(values)
    distinct = len(set(values))
    return distinct, total, (distinct / total if total else 0.0)


def is_low_information_numeric_column(values_by_row: dict[int, Decimal], params: PairForensicsParams) -> bool:
    """Treat low-cardinality numeric columns as annotation columns, not measurements."""
    distinct, total, diversity = numeric_value_diversity(values_by_row)
    if total < params.min_pairs:
        return False
    return distinct <= 3 or (total >= 20 and diversity <= 0.1)


def candidate_offsets(rows: list[int], params: PairForensicsParams) -> list[int]:
    if not rows:
        return []
    span = max(rows) - min(rows)
    max_offset = min(params.max_offset, span)
    offsets = []
    for offset in range(1, max_offset + 1):
        if len(common_offset_pairs(rows, offset)) >= params.min_pairs:
            offsets.append(offset)
    return offsets


def row_offset_scalar_findings(sheet: SheetVectors, params: PairForensicsParams) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str, str], dict[str, Any]] = {}
    for col, values_by_row in sorted(sheet.numeric_columns.items()):
        if is_low_information_numeric_column(values_by_row, params):
            continue
        rows = sorted(values_by_row)
        for offset in candidate_offsets(rows, params):
            pairs = common_offset_pairs(rows, offset)
            ratios: Counter[str] = Counter()
            ratio_rows: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for left_row, right_row in pairs:
                key = ratio_key(values_by_row[right_row], values_by_row[left_row], params.ratio_places)
                if key is None:
                    continue
                ratios[key] += 1
                ratio_rows[key].append((left_row, right_row))
            if not ratios:
                continue
            value, count = ratios.most_common(1)[0]
            support_rate = count / len(pairs)
            if count < params.min_pairs or support_rate < params.min_support:
                continue
            category = "row_offset_exact_reuse" if value == "1" else "row_offset_scalar_multiple"
            formula_involved = col in sheet.formulas_by_column
            risk = "high" if count >= 10 and value != "1" else "medium"
            if formula_involved:
                risk = "medium"
            group_key = (offset, value, category)
            group = grouped.setdefault(
                group_key,
                {
                    "finding_id": None,
                    "category": category,
                    "risk_level": risk,
                    "confidence": "high" if support_rate >= 0.98 else "medium",
                    "workbook": sheet.workbook,
                    "sheet": sheet.sheet,
                    "row_offset": offset,
                    "relationship_value": value,
                    "support_rows": 0,
                    "overlap_rows": 0,
                    "support_rate": 0.0,
                    "columns": [],
                    "column_labels": [],
                    "sample_pairs": [],
                    "formula_column_involved": False,
                    "benign_explanations": [
                        "可能是合法批量归一化、单位换算、重复测量整理或设计矩阵编码。",
                        "若行代表独立样本或独立患者，固定行偏移倍数关系需要人工复核。",
                    ],
                    "pressure_test_result": "needs_row_offset_independence_review",
                    "next_steps": [
                        "确认行是否代表独立样本、独立患者或分组后的重复计算结果。",
                        "核对第 N 行和第 N+offset 行是否应具有独立测量来源。",
                        "要求原始仪器导出、图像分析日志或上游计算产物支持独立性。",
                    ],
                },
            )
            group["risk_level"] = max(group["risk_level"], risk, key=risk_rank)
            group["confidence"] = "high" if group["confidence"] == "high" or support_rate >= 0.98 else "medium"
            group["support_rows"] += count
            group["overlap_rows"] += len(pairs)
            group["support_rate"] = round(group["support_rows"] / group["overlap_rows"], 4)
            group["columns"].append(col_to_name(col))
            label = column_label(sheet, col)
            group["column_labels"].append(label)
            group["formula_column_involved"] = group["formula_column_involved"] or formula_involved
            for left_row, right_row in ratio_rows[value][:5]:
                if len(group["sample_pairs"]) >= 20:
                    break
                group["sample_pairs"].append(
                    {
                        "left_row": left_row,
                        "right_row": right_row,
                        "column": col_to_name(col),
                        "left": normalized_number(values_by_row[left_row]),
                        "right": normalized_number(values_by_row[right_row]),
                        "ratio": value,
                    }
                )
    findings = list(grouped.values())
    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item["support_rows"]))[
        : params.max_findings_per_category
    ]


def paired_ratio_reuse_findings(sheet: SheetVectors, params: PairForensicsParams) -> list[dict[str, Any]]:
    columns = sorted(sheet.numeric_columns)
    findings = []
    for left_index, left_col in enumerate(columns):
        if is_low_information_numeric_column(sheet.numeric_columns[left_col], params):
            continue
        for right_col in columns[left_index + 1 :]:
            if is_low_information_numeric_column(sheet.numeric_columns[right_col], params):
                continue
            common = sorted(set(sheet.numeric_columns[left_col]).intersection(sheet.numeric_columns[right_col]))
            if len(common) < params.min_pairs * 2:
                continue
            ratios_by_row = {}
            for row in common:
                key = ratio_key(
                    sheet.numeric_columns[right_col][row],
                    sheet.numeric_columns[left_col][row],
                    params.ratio_places,
                )
                if key is not None:
                    ratios_by_row[row] = key
            rows = sorted(ratios_by_row)
            for offset in candidate_offsets(rows, params):
                pairs = common_offset_pairs(rows, offset)
                matched = [(left_row, right_row) for left_row, right_row in pairs if ratios_by_row[left_row] == ratios_by_row[right_row]]
                support_rate = len(matched) / len(pairs) if pairs else 0
                if len(matched) < params.min_pairs or support_rate < params.min_support:
                    continue
                risk = "high" if len(matched) >= 10 and support_rate >= 0.95 else "medium"
                findings.append(
                    {
                        "finding_id": None,
                        "category": "paired_ratio_reuse",
                        "risk_level": risk,
                        "confidence": "high" if support_rate >= 0.98 else "medium",
                        "workbook": sheet.workbook,
                        "sheet": sheet.sheet,
                        "row_offset": offset,
                        "column_pair": [col_to_name(left_col), col_to_name(right_col)],
                        "column_labels": [column_label(sheet, left_col), column_label(sheet, right_col)],
                        "matched_pairs": len(matched),
                        "overlap_pairs": len(pairs),
                        "support_rate": round(support_rate, 4),
                        "ratio_places": params.ratio_places,
                        "sample_pairs": [
                            {
                                "left_row": left_row,
                                "right_row": right_row,
                                "left_ratio": ratios_by_row[left_row],
                                "right_ratio": ratios_by_row[right_row],
                                "reconstruction": (
                                    f"{col_to_name(right_col)}{right_row} ~= "
                                    f"{col_to_name(left_col)}{right_row} * "
                                    f"{col_to_name(right_col)}{left_row}/{col_to_name(left_col)}{left_row}"
                                ),
                            }
                            for left_row, right_row in matched[:20]
                        ],
                        "benign_explanations": [
                            "可能是重复展示同一配对比值、标准化后派生指标或批次归一化产物。",
                            "若行代表独立配对样本，N 与 N+offset 的比例复用需要人工复核。",
                        ],
                        "pressure_test_result": "needs_pair_ratio_independence_review",
                        "next_steps": [
                            "确认两列是否构成 paired ratio，例如 PT/RT、pre/post、control/treatment。",
                            "复算第 N 行与第 N+offset 行的 ratio 是否来自独立原始测量。",
                            "要求原始仪器输出或上游分析日志验证样本独立性。",
                        ],
                    }
                )
    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item["matched_pairs"]))[
        : params.max_findings_per_category
    ]


def duplicate_row_vector_findings(sheet: SheetVectors, params: PairForensicsParams) -> list[dict[str, Any]]:
    row_vectors: dict[tuple[tuple[int, str], ...], list[int]] = defaultdict(list)
    high_information_columns = {
        col
        for col, values_by_row in sheet.numeric_columns.items()
        if not is_low_information_numeric_column(values_by_row, params)
    }
    for row in sorted({row for values in sheet.numeric_columns.values() for row in values}):
        vector = []
        for col in sorted(sheet.numeric_columns):
            value = sheet.numeric_columns[col].get(row)
            if value is not None:
                vector.append((col, normalized_number(value)))
        if len(vector) >= params.min_duplicate_row_width and any(col in high_information_columns for col, _value in vector):
            row_vectors[tuple(vector)].append(row)

    findings = []
    for vector, rows in row_vectors.items():
        if len(rows) < 2:
            continue
        cols = [col for col, _value in vector]
        findings.append(
            {
                "finding_id": None,
                "category": "duplicate_row_vector",
                "risk_level": "medium" if len(rows) < 4 else "high",
                "confidence": "high",
                "workbook": sheet.workbook,
                "sheet": sheet.sheet,
                "rows": rows[:30],
                "duplicate_row_count": len(rows),
                "width": len(vector),
                "columns": [col_to_name(col) for col in cols],
                "column_labels": [column_label(sheet, col) for col in cols],
                "values": [value for _col, value in vector],
                "benign_explanations": [
                    "可能是合法重复测量、重复展示同一样本、分组模板行或空值填充后结果。",
                    "若行代表不同独立样本，整行数值向量重复需要人工复核。",
                ],
                "pressure_test_result": "needs_duplicate_row_semantics_review",
                "next_steps": [
                    "核对重复行的样本 ID、分组、图表 panel 和是否为独立测量。",
                    "确认重复行是否影响论文中的 n、统计检验或效应量。",
                ],
            }
        )
    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item["duplicate_row_count"], -item["width"]))[
        : params.max_findings_per_category
    ]


def long_format_pair_groups(
    sheet: SheetVectors,
    id_col: int,
    value_col: int,
    params: PairForensicsParams,
) -> dict[int, tuple[int, int]]:
    id_values = sheet.numeric_columns[id_col]
    value_values = sheet.numeric_columns[value_col]
    groups: dict[int, list[int]] = defaultdict(list)
    for row, pair_id in id_values.items():
        if row not in value_values:
            continue
        if pair_id != pair_id.to_integral_value():
            continue
        groups[int(pair_id)].append(row)
    if len(groups) < params.min_pairs:
        return {}
    paired = {pair_id: tuple(sorted(rows)) for pair_id, rows in groups.items() if len(rows) == 2}
    if len(paired) < params.min_pairs:
        return {}
    if len(paired) / len(groups) < 0.75:
        return {}
    return paired


def long_format_pair_ratios(
    value_values: dict[int, Decimal],
    pair_groups: dict[int, tuple[int, int]],
    params: PairForensicsParams,
) -> dict[int, str]:
    ratios = {}
    for pair_id, rows in pair_groups.items():
        first_row, second_row = rows
        key = ratio_key(value_values[second_row], value_values[first_row], params.ratio_places)
        if key is not None:
            ratios[pair_id] = key
    return ratios


def long_format_paired_ratio_reuse_findings(sheet: SheetVectors, params: PairForensicsParams) -> list[dict[str, Any]]:
    findings = []
    columns = sorted(sheet.numeric_columns)
    for id_col in columns:
        id_values = sheet.numeric_columns[id_col]
        distinct, total, _diversity = numeric_value_diversity(id_values)
        if distinct < params.min_pairs or total < params.min_pairs * 2:
            continue
        for value_col in columns:
            if value_col == id_col:
                continue
            value_values = sheet.numeric_columns[value_col]
            if is_low_information_numeric_column(value_values, params):
                continue
            pair_groups = long_format_pair_groups(sheet, id_col, value_col, params)
            if not pair_groups:
                continue
            ratios_by_pair = long_format_pair_ratios(value_values, pair_groups, params)
            pair_ids = sorted(ratios_by_pair)
            for offset in candidate_offsets(pair_ids, params):
                pairs = common_offset_pairs(pair_ids, offset)
                matched = [
                    (left_id, right_id)
                    for left_id, right_id in pairs
                    if ratios_by_pair.get(left_id) == ratios_by_pair.get(right_id)
                ]
                support_rate = len(matched) / len(pairs) if pairs else 0
                if len(matched) < params.min_pairs or support_rate < params.min_support:
                    continue
                findings.append(
                    {
                        "finding_id": None,
                        "category": "long_format_paired_ratio_reuse",
                        "risk_level": "high" if len(matched) >= 10 else "medium",
                        "confidence": "high" if support_rate >= 0.98 else "medium",
                        "workbook": sheet.workbook,
                        "sheet": sheet.sheet,
                        "pair_id_offset": offset,
                        "columns": [col_to_name(id_col), col_to_name(value_col)],
                        "id_column": col_to_name(id_col),
                        "value_column": col_to_name(value_col),
                        "column_labels": [column_label(sheet, id_col), column_label(sheet, value_col)],
                        "matched_pair_groups": len(matched),
                        "overlap_pair_groups": len(pairs),
                        "support_rate": round(support_rate, 4),
                        "ratio_places": params.ratio_places,
                        "sample_pairs": [
                            {
                                "left_pair_id": left_id,
                                "right_pair_id": right_id,
                                "left_rows": list(pair_groups[left_id]),
                                "right_rows": list(pair_groups[right_id]),
                                "left_ratio": ratios_by_pair[left_id],
                                "right_ratio": ratios_by_pair[right_id],
                                "reconstruction": (
                                    f"{col_to_name(value_col)}{pair_groups[right_id][1]} ~= "
                                    f"{col_to_name(value_col)}{pair_groups[right_id][0]} * "
                                    f"{col_to_name(value_col)}{pair_groups[left_id][1]}/"
                                    f"{col_to_name(value_col)}{pair_groups[left_id][0]}"
                                ),
                            }
                            for left_id, right_id in matched[:20]
                        ],
                        "benign_explanations": [
                            "可能是合法成对样本的标准化比例复用、批次校正或派生指标。",
                            "若 pair id 代表独立患者或独立样本，pair N 与 N+offset 的比值复用需要人工复核。",
                        ],
                        "pressure_test_result": "needs_long_format_pair_ratio_independence_review",
                        "next_steps": [
                            "确认 id_column 是否为患者、样本或 pair 编号，且每个 pair 是否只有两个条件。",
                            "核对同一 pair 内两行是否代表 PT/RT、pre/post、control/treatment 等成对测量。",
                            "要求原始仪器输出、上游分析日志或代码产物验证后半段 pair 的独立性。",
                        ],
                    }
                )
    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item["matched_pair_groups"]))[
        : params.max_findings_per_category
    ]


def long_format_within_pair_ratio_enrichment_findings(sheet: SheetVectors, params: PairForensicsParams) -> list[dict[str, Any]]:
    findings = []
    columns = sorted(sheet.numeric_columns)
    for id_col in columns:
        id_values = sheet.numeric_columns[id_col]
        distinct, total, _diversity = numeric_value_diversity(id_values)
        if distinct < params.min_pairs or total < params.min_pairs * 2:
            continue
        for value_col in columns:
            if value_col == id_col:
                continue
            value_values = sheet.numeric_columns[value_col]
            if is_low_information_numeric_column(value_values, params):
                continue
            pair_groups = long_format_pair_groups(sheet, id_col, value_col, params)
            if not pair_groups:
                continue
            ratios_by_pair = long_format_pair_ratios(value_values, pair_groups, params)
            if len(ratios_by_pair) < params.min_pairs:
                continue
            ratio_counts = Counter(ratios_by_pair.values())
            for ratio, count in ratio_counts.most_common(params.max_findings_per_category):
                if ratio == "1":
                    continue
                support_rate = count / len(ratios_by_pair)
                # Repeated within-pair ratios are weaker evidence than row-offset
                # reuse; require a minimum absolute count and meaningful prevalence.
                if count < params.min_pairs or support_rate < 0.2:
                    continue
                matched_pair_ids = [pair_id for pair_id, value in ratios_by_pair.items() if value == ratio]
                findings.append(
                    {
                        "finding_id": None,
                        "category": "long_format_within_pair_ratio_enrichment",
                        "risk_level": "medium" if support_rate < 0.5 else "high",
                        "confidence": "medium",
                        "workbook": sheet.workbook,
                        "sheet": sheet.sheet,
                        "columns": [col_to_name(id_col), col_to_name(value_col)],
                        "id_column": col_to_name(id_col),
                        "value_column": col_to_name(value_col),
                        "column_labels": [column_label(sheet, id_col), column_label(sheet, value_col)],
                        "relationship_value": ratio,
                        "matched_pair_groups": count,
                        "overlap_pair_groups": len(ratios_by_pair),
                        "support_rate": round(support_rate, 4),
                        "ratio_places": params.ratio_places,
                        "sample_pair_ids": matched_pair_ids[:30],
                        "sample_pairs": [
                            {
                                "pair_id": pair_id,
                                "rows": list(pair_groups[pair_id]),
                                "ratio": ratio,
                            }
                            for pair_id in matched_pair_ids[:20]
                        ],
                        "benign_explanations": [
                            "可能是阈值化、归一化、分箱或整数比例编码导致的合法重复比例。",
                            "若该比例跨多个独立患者或样本精确重复，应检查是否来自派生或复制过程。",
                        ],
                        "pressure_test_result": "needs_repeated_within_pair_ratio_review",
                        "next_steps": [
                            "确认重复比例是否由方法学定义、阈值化或归一化流程预期产生。",
                            "如果比例代表独立测量结果，抽查原始数据和生成脚本。",
                        ],
                    }
                )
    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item["matched_pair_groups"]))[
        : params.max_findings_per_category
    ]


def row_offset_rounding_bias_findings(sheet: SheetVectors, params: PairForensicsParams) -> list[dict[str, Any]]:
    findings = []
    for col, values_by_row in sorted(sheet.numeric_columns.items()):
        if is_low_information_numeric_column(values_by_row, params):
            continue
        rows = sorted(values_by_row)
        for offset in candidate_offsets(rows, params):
            pairs = common_offset_pairs(rows, offset)
            exact = []
            rounded_second = []
            upward_changes = []
            comparable = []
            for left_row, right_row in pairs:
                left = values_by_row[left_row]
                right = values_by_row[right_row]
                comparable.append((left_row, right_row))
                if left == right:
                    exact.append((left_row, right_row))
                if decimal_places(right) <= 2 and decimal_places(left) >= 6:
                    rounded_second.append((left_row, right_row))
                if right > left:
                    upward_changes.append((left_row, right_row))
            if len(comparable) < params.min_pairs:
                continue
            exact_rate = len(exact) / len(comparable)
            rounded_rate = len(rounded_second) / len(comparable)
            upward_rate = len(upward_changes) / max(1, len([pair for pair in comparable if values_by_row[pair[0]] != values_by_row[pair[1]]]))
            # This is intentionally stricter than scalar/ratio reuse. Precision
            # shifts are noisy in spreadsheets; only emit when exact reuse,
            # coarse second-block values, and directional changes co-occur.
            if exact_rate < 0.5 or rounded_rate < 0.3 or upward_rate < 0.75:
                continue
            findings.append(
                {
                    "finding_id": None,
                    "category": "row_offset_partial_copy_rounding_bias",
                    "risk_level": "high",
                    "confidence": "medium",
                    "workbook": sheet.workbook,
                    "sheet": sheet.sheet,
                    "row_offset": offset,
                    "column": col_to_name(col),
                    "column_label": column_label(sheet, col),
                    "overlap_pairs": len(comparable),
                    "exact_reuse_pairs": len(exact),
                    "rounded_second_block_pairs": len(rounded_second),
                    "upward_change_rate": round(upward_rate, 4),
                    "exact_reuse_rate": round(exact_rate, 4),
                    "rounded_second_block_rate": round(rounded_rate, 4),
                    "sample_exact_pairs": [{"left_row": left, "right_row": right} for left, right in exact[:10]],
                    "sample_rounded_pairs": [{"left_row": left, "right_row": right} for left, right in rounded_second[:10]],
                    "benign_explanations": [
                        "可能是人工四舍五入、单位换算后展示值或不同精度导出的合法结果。",
                        "若后半区代表新增独立样本，精度骤降和单向修改需要人工复核。",
                    ],
                    "pressure_test_result": "needs_partial_copy_rounding_review",
                    "next_steps": [
                        "比较前后区间的原始导出精度和修改方向。",
                        "确认后半区样本是否具有独立原始记录。",
                    ],
                }
            )
    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item["exact_reuse_pairs"]))[
        : params.max_findings_per_category
    ]


def assign_ids(findings: list[dict[str, Any]]) -> None:
    counters: Counter[str] = Counter()
    prefixes = {
        "row_offset_exact_reuse": "ROE",
        "row_offset_scalar_multiple": "ROS",
        "paired_ratio_reuse": "PRR",
        "duplicate_row_vector": "DRV",
        "long_format_paired_ratio_reuse": "LPR",
        "long_format_within_pair_ratio_enrichment": "LPE",
        "row_offset_partial_copy_rounding_bias": "RBR",
    }
    for finding in findings:
        category = finding["category"]
        counters[category] += 1
        finding["finding_id"] = f"{prefixes.get(category, 'PF')}-{counters[category]:04d}"


def analyze_xlsx_root(xlsx_root: Path, params: PairForensicsParams) -> dict[str, Any]:
    errors = []
    scalar_findings = []
    ratio_findings = []
    duplicate_rows = []
    long_ratio_reuse = []
    long_ratio_enrichment = []
    rounding_bias = []
    workbook_count = 0
    sheet_count = 0
    for workbook_path in sorted(xlsx_root.glob("*.xlsx")):
        workbook_count += 1
        try:
            sheets = parse_workbook_vectors(workbook_path)
        except Exception as exc:
            errors.append({"workbook": workbook_path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        sheet_count += len(sheets)
        for sheet in sheets:
            scalar_findings.extend(row_offset_scalar_findings(sheet, params))
            ratio_findings.extend(paired_ratio_reuse_findings(sheet, params))
            duplicate_rows.extend(duplicate_row_vector_findings(sheet, params))
            long_ratio_reuse.extend(long_format_paired_ratio_reuse_findings(sheet, params))
            long_ratio_enrichment.extend(long_format_within_pair_ratio_enrichment_findings(sheet, params))
            rounding_bias.extend(row_offset_rounding_bias_findings(sheet, params))

    findings = [
        *scalar_findings,
        *ratio_findings,
        *duplicate_rows,
        *long_ratio_reuse,
        *long_ratio_enrichment,
        *rounding_bias,
    ]
    findings = sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), str(item.get("workbook")), str(item.get("sheet"))))
    assign_ids(findings)
    priority_findings = [finding for finding in findings if risk_rank(finding.get("risk_level", "")) >= 2]
    by_category = Counter(finding["category"] for finding in findings)
    return {
        "schema_version": "1.0",
        "created_by": "engine/static_audit/tools/source_data_pair_forensics.py",
        "inputs": {"xlsx_root": str(xlsx_root)},
        "parameters": {
            "min_pairs": params.min_pairs,
            "min_support": params.min_support,
            "ratio_places": params.ratio_places,
            "max_offset": params.max_offset,
            "max_findings_per_category": params.max_findings_per_category,
            "min_duplicate_row_width": params.min_duplicate_row_width,
        },
        "summary": {
            "workbook_count": workbook_count,
            "sheet_count": sheet_count,
            "findings": len(findings),
            "priority_findings": len(priority_findings),
            "row_offset_scalar_findings": len(scalar_findings),
            "paired_ratio_reuse_findings": len(ratio_findings),
            "duplicate_row_vector_findings": len(duplicate_rows),
            "long_format_paired_ratio_reuse_findings": len(long_ratio_reuse),
            "long_format_within_pair_ratio_enrichment_findings": len(long_ratio_enrichment),
            "rounding_bias_findings": len(rounding_bias),
            "by_category": dict(by_category),
            "errors": len(errors),
        },
        "findings": findings,
        "priority_findings": priority_findings,
        "row_offset_scalar_findings": scalar_findings,
        "paired_ratio_reuse_findings": ratio_findings,
        "duplicate_row_vector_findings": duplicate_rows,
        "long_format_paired_ratio_reuse_findings": long_ratio_reuse,
        "long_format_within_pair_ratio_enrichment_findings": long_ratio_enrichment,
        "rounding_bias_findings": rounding_bias,
        "errors": errors,
        "limitations": [
            "该工具只识别 XLSX 中的通用行偏移、配对比例复用、long-format 成对比例复用和低宽度行重复模式，不判断最终科研诚信。",
            "行是否代表独立样本、患者或技术重复需要结合 sheet 注释、论文方法和原始仪器输出人工确认。",
            "ratio_places 会影响 paired ratio reuse 的敏感度；高精度与展示值四舍五入场景应分开解释。",
            "低信息数值列会被视为分组/类别/编号候选并排除在连续测量列检测之外，可能降低二分类测量场景的敏感度。",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect generic paired-cohort and row-offset patterns in XLSX Source Data.")
    parser.add_argument("xlsx_root", help="Directory containing .xlsx source data files.")
    parser.add_argument("--output", required=True, help="Output source_data_pair_forensics.json path.")
    parser.add_argument("--min-pairs", type=int, default=8)
    parser.add_argument("--min-support", type=float, default=0.95)
    parser.add_argument("--ratio-places", type=int, default=4)
    parser.add_argument("--max-offset", type=int, default=80)
    parser.add_argument("--max-findings-per-category", type=int, default=50)
    parser.add_argument("--min-duplicate-row-width", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    xlsx_root = Path(args.xlsx_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    params = PairForensicsParams(
        min_pairs=max(2, args.min_pairs),
        min_support=min(1.0, max(0.0, args.min_support)),
        ratio_places=max(1, args.ratio_places),
        max_offset=max(1, args.max_offset),
        max_findings_per_category=max(1, args.max_findings_per_category),
        min_duplicate_row_width=max(2, args.min_duplicate_row_width),
    )
    result = analyze_xlsx_root(xlsx_root, params)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
