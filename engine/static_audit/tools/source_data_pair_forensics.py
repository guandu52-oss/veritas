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
    # Small-group detectors: biological replicates typically have n=3–6,
    # far below the default min_pairs=8.  These parameters let us detect
    # fixed arithmetic relationships, perfect duplicates, and cross-sheet
    # decimal reuse in groups as small as 2–3 values.
    min_small_group_size: int = 2
    max_small_group_size: int = 7
    min_decimal_match_length: int = 6
    decimal_match_max_diff_places: int = 1
    min_cross_sheet_fraction: float = 0.50
    min_cross_sheet_matches: int = 6


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


# ---------------------------------------------------------------------------
# Small-group detectors (n = 2–7 biological replicates)
# ---------------------------------------------------------------------------


def _is_round_diff(diff: Decimal, max_places: int) -> bool:
    """True when *diff* has ≤ *max_places* significant decimal places.

    Examples (max_places=2):
      0.40  → True   (2 places)
      1.00  → True   (0 places after normalisation)
      2.80  → True   (2 places)
      0.09593023 → False (8 significant places)
    """
    if diff == 0:
        return True
    # Remove trailing zeros without changing the value
    normalized = diff.normalize()
    _, digits, exponent = normalized.as_tuple()
    if digits == (0,):
        return True
    # Significant decimal places = -exponent
    places = max(0, -exponent) if isinstance(exponent, int) else 0
    return places <= max_places


def _decimal_tail_match_len(a: Decimal, b: Decimal, *, max_digits: int = 12) -> int:
    """Return N where the last N decimal digits of *a* and *b* are identical.

    Both values are first quantized to *max_digits* decimal places to
    neutralise IEEE-754 floating-point noise that leaks through XLSX XML
    parsing (e.g. ``5.438083`` stored as ``5.4380829999999998``).
    """
    quant = Decimal(1).scaleb(-max_digits)
    qa = a.quantize(quant)
    qb = b.quantize(quant)
    sa, sb = str(qa), str(qb)
    if "." not in sa or "." not in sb:
        return 0
    da = sa.split(".")[1]
    db = sb.split(".")[1]
    n = 0
    for ca, cb in zip(reversed(da), reversed(db)):
        if ca != cb:
            break
        n += 1
    return n


def _is_small_group_column(values_by_row: dict[int, Decimal], params: PairForensicsParams) -> bool:
    """True when the column is small enough for small-group detectors."""
    return 2 <= len(values_by_row) <= params.max_small_group_size


def small_group_fixed_relationship_findings(
    sheet: SheetVectors, params: PairForensicsParams
) -> list[dict[str, Any]]:
    """Detect fixed arithmetic relationships (offsets / ratios) in small groups.

    Biological replicates (n=3–6) are too small for the main row-offset
    detectors (min_pairs=8).  This detector targets precisely those groups:
    when 2–7 values in a column share a fixed intra-column offset or when
    two columns are linked by a fixed arithmetic rule.
    """
    findings: list[dict[str, Any]] = []
    small_cols = {
        col: values
        for col, values in sheet.numeric_columns.items()
        if _is_small_group_column(values, params)
    }
    if not small_cols:
        return findings

    # --- within-column: same offset/ratio across multiple pairs ---
    for col, values_by_row in sorted(small_cols.items()):
        rows = sorted(values_by_row)
        if len(rows) < params.min_small_group_size:
            continue
        row_values = [(r, values_by_row[r]) for r in rows]

        # All pairwise diffs
        diffs: Counter[Decimal] = Counter()
        ratio_groups: dict[str, list[tuple[int, int, Decimal]]] = defaultdict(list)
        for i in range(len(row_values)):
            for j in range(i + 1, len(row_values)):
                ri, vi = row_values[i]
                rj, vj = row_values[j]
                if vi == vj:
                    continue
                d = abs(vj - vi)
                diffs[d] += 1
                rk = ratio_key(vj, vi, params.ratio_places)
                if rk is not None and rk != "1":
                    ratio_groups[rk].append((ri, rj, d))

        for diff_val, count in diffs.most_common(5):
            if count < params.min_small_group_size:
                break
            risk = "high" if count >= 3 else "medium"
            findings.append(
                {
                    "finding_id": None,
                    "category": "small_group_fixed_relationship",
                    "sub_category": "fixed_offset",
                    "risk_level": risk,
                    "confidence": "high" if count >= 3 else "medium",
                    "workbook": sheet.workbook,
                    "sheet": sheet.sheet,
                    "column": col_to_name(col),
                    "column_label": column_label(sheet, col),
                    "relationship_value": str(diff_val.normalize()),
                    "matched_pairs": count,
                    "total_rows": len(rows),
                    "values": [normalized_number(values_by_row[r]) for r in rows],
                    "benign_explanations": [
                        "当列代表独立生物学重复时，小样本组内的精确固定差异无法由测量误差解释。",
                        "可能是数据衍生、公式生成或人工构造的信号。",
                    ],
                    "pressure_test_result": "needs_small_group_offset_review",
                    "next_steps": [
                        "确认该列值是否代表独立生物学重复或技术重复。",
                        "要求原始仪器导出或实验记录验证值的独立性。",
                    ],
                }
            )

        # Ratio reuse
        for ratio, pairs in ratio_groups.items():
            if len(pairs) < params.min_small_group_size:
                continue
            risk = "high" if len(pairs) >= 3 else "medium"
            findings.append(
                {
                    "finding_id": None,
                    "category": "small_group_fixed_relationship",
                    "sub_category": "fixed_ratio",
                    "risk_level": risk,
                    "confidence": "high" if len(pairs) >= 3 else "medium",
                    "workbook": sheet.workbook,
                    "sheet": sheet.sheet,
                    "column": col_to_name(col),
                    "column_label": column_label(sheet, col),
                    "relationship_value": ratio,
                    "matched_pairs": len(pairs),
                    "total_rows": len(rows),
                    "sample_pairs": [
                        {
                            "left_row": left,
                            "right_row": right,
                            "left": normalized_number(values_by_row[left]),
                            "right": normalized_number(values_by_row[right]),
                            "ratio": ratio,
                        }
                        for left, right, _diff in pairs[:10]
                    ],
                    "benign_explanations": [
                        "当列代表独立生物学重复时，小样本组内的精确固定比例无法由测量误差解释。",
                    ],
                    "pressure_test_result": "needs_small_group_ratio_review",
                    "next_steps": [
                        "确认该比例是否来自已知归一化或换算公式。",
                        "要求原始数据验证。",
                    ],
                }
            )

    # --- cross-column: fixed offset between two small columns ---
    small_col_list = sorted(small_cols)
    for ai in range(len(small_col_list)):
        col_a = small_col_list[ai]
        vals_a = small_cols[col_a]
        rows_a = sorted(vals_a)
        if len(rows_a) < params.min_small_group_size:
            continue
        for bi in range(ai + 1, len(small_col_list)):
            col_b = small_col_list[bi]
            vals_b = small_cols[col_b]
            rows_b = sorted(vals_b)
            if len(rows_b) != len(rows_a):
                continue
            # Map by row index position
            diffs_ab = []
            for (ra, va), (rb, vb) in zip(sorted(vals_a.items()), sorted(vals_b.items())):
                if va != vb:
                    diffs_ab.append(abs(vb - va))
            if len(diffs_ab) < params.min_small_group_size:
                continue
            # Check if all diffs are the same
            unique_diffs = set(diffs_ab)
            if len(unique_diffs) == 1 and len(diffs_ab) >= params.min_small_group_size:
                diff = diffs_ab[0]
                risk = "high" if len(diffs_ab) >= 3 else "medium"
                findings.append(
                    {
                        "finding_id": None,
                        "category": "small_group_fixed_relationship",
                        "sub_category": "cross_column_fixed_offset",
                        "risk_level": risk,
                        "confidence": "high",
                        "workbook": sheet.workbook,
                        "sheet": sheet.sheet,
                        "columns": [col_to_name(col_a), col_to_name(col_b)],
                        "column_labels": [column_label(sheet, col_a), column_label(sheet, col_b)],
                        "relationship_value": str(diff.normalize()),
                        "matched_pairs": len(diffs_ab),
                        "sample_pairs": [
                            {
                                "col_a": col_to_name(col_a),
                                "col_b": col_to_name(col_b),
                                f"row_{ra}": normalized_number(va),
                                f"row_{rb}": normalized_number(vb),
                                "diff": str(diff.normalize()),
                            }
                            for (ra, va), (rb, vb) in list(zip(sorted(vals_a.items()), sorted(vals_b.items())))[:10]
                        ],
                        "benign_explanations": [
                            "两列之间所有行对具有完全相同的差值，独立生物学测量中不可能出现。",
                            "可能是数据复制粘贴、公式生成或批量填充的信号。",
                        ],
                        "pressure_test_result": "needs_cross_column_offset_review",
                        "next_steps": [
                            "验证两列是否代表独立的实验条件或组别。",
                            "核对原始仪器输出确认各列值的独立性。",
                        ],
                    }
                )

    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item.get("matched_pairs", 0)))[
        : params.max_findings_per_category
    ]


def perfect_duplicate_value_findings(
    sheet: SheetVectors, params: PairForensicsParams
) -> list[dict[str, Any]]:
    """Detect identical numeric values that appear across all/most rows of a column.

    In biological experiments, independent replicates always exhibit variation
    (typically CV 5–20%).  A value appearing in every row of a column — or
    dominating a column — is a strong signal of data fabrication.
    """
    findings: list[dict[str, Any]] = []
    for col, values_by_row in sorted(sheet.numeric_columns.items()):
        total = len(values_by_row)
        if total < 3:
            continue
        if is_low_information_numeric_column(values_by_row, params):
            continue
        value_counts: Counter[Decimal] = Counter()
        for v in values_by_row.values():
            value_counts[normalized_number(v)] += 1

        for value, count in value_counts.most_common(10):
            if count < 3:
                break
            dominance = count / total
            if count == total:
                risk = "critical"
                sub = "all_rows_identical"
            elif dominance >= 0.5:
                risk = "high"
                sub = "majority_rows_identical"
            else:
                continue

            findings.append(
                {
                    "finding_id": None,
                    "category": "perfect_duplicate_values",
                    "sub_category": sub,
                    "risk_level": risk,
                    "confidence": "high",
                    "workbook": sheet.workbook,
                    "sheet": sheet.sheet,
                    "column": col_to_name(col),
                    "column_label": column_label(sheet, col),
                    "duplicate_value": value,
                    "duplicate_count": count,
                    "column_total": total,
                    "dominance": round(dominance, 4),
                    "benign_explanations": [
                        "可能在极特殊情况下合法：常数值（如固定浓度）、全缺失填充标记、或技术噪声远小于展示精度。",
                        "然而真正的生物学独立重复测量几乎一定有数值波动。",
                    ],
                    "pressure_test_result": "needs_perfect_duplicate_review",
                    "next_steps": [
                        f"确认该列是否代表 {total} 次独立生物学重复。",
                        "如是独立重复，要求提供原始仪器导出或实验记录。",
                        "核对论文中是否将相同值报道为不同样本的独立测量结果。",
                    ],
                }
            )
    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item.get("duplicate_count", 0)))[
        : params.max_findings_per_category
    ]


def _build_suffix_index(
    values: list[Decimal], min_match_len: int, max_digits: int = 12
) -> defaultdict[str, list[Decimal]]:
    """Build a hash index keyed by the last *min_match_len* fractional digits.

    Two values that share at least *min_match_len* trailing decimal digits
    must have the same suffix of length *min_match_len*, so they land in the
    same bucket.  This reduces the cross-sheet match inner loop from O(n×m)
    to O(n × avg_bucket_size).
    """
    index: defaultdict[str, list[Decimal]] = defaultdict(list)
    quant = Decimal(1).scaleb(-max_digits)
    for v in values:
        sv = str(v.quantize(quant))
        if "." not in sv:
            continue
        frac = sv.split(".")[1]
        if len(frac) < min_match_len:
            continue
        suffix = frac[-min_match_len:]
        index[suffix].append(v)
    return index


def cross_sheet_decimal_match_findings(
    all_sheets: list[SheetVectors], params: PairForensicsParams
) -> list[dict[str, Any]]:
    """Detect sheets that share implausible decimal-tail patterns.

    For each value in the *smaller* sheet, the detector finds the single
    best-matching value in the other sheet (longest trailing-decimal match
    with a round difference).  This caps matches at min(|A|,|B|) and avoids
    the combinatorial explosion of O(n²) all-pairs comparison.

    A suffix-hash index on the larger sheet reduces the inner loop from
    O(n×m) to O(n × avg_bucket_size).
    """
    findings: list[dict[str, Any]] = []
    if len(all_sheets) < 2:
        return findings

    # Pre-compute per-sheet value lists
    sheet_data: dict[tuple[str, str], list[Decimal]] = {}
    for sheet in all_sheets:
        key = (sheet.workbook, sheet.sheet)
        vals: list[Decimal] = []
        for col, values_by_row in sheet.numeric_columns.items():
            if is_low_information_numeric_column(values_by_row, params):
                continue
            vals.extend(values_by_row.values())
        if vals:
            sheet_data[key] = vals

    sheet_keys = sorted(sheet_data)
    for ai in range(len(sheet_keys)):
        key_a = sheet_keys[ai]
        vals_a = sheet_data[key_a]
        for bi in range(ai + 1, len(sheet_keys)):
            key_b = sheet_keys[bi]
            vals_b = sheet_data[key_b]

            # Let the smaller sheet drive the comparison
            if len(vals_a) <= len(vals_b):
                smaller_vals, larger_vals = vals_a, vals_b
            else:
                smaller_vals, larger_vals = vals_b, vals_a

            # Build suffix index on the larger sheet for O(1) bucket lookup
            suffix_index = _build_suffix_index(larger_vals, params.min_decimal_match_length)

            matched: list[dict] = []
            for v_small in smaller_vals:
                best_n = 0
                best_v: Decimal | None = None
                best_diff: Decimal | None = None
                # Get suffix for v_small to look up the correct bucket
                sv_small = str(v_small.quantize(Decimal(1).scaleb(-12)))
                if "." not in sv_small:
                    continue
                frac_small = sv_small.split(".")[1]
                if len(frac_small) < params.min_decimal_match_length:
                    continue
                candidate_key = frac_small[-params.min_decimal_match_length:]
                # Only compare against values in the same suffix bucket
                for v_large in suffix_index.get(candidate_key, ()):
                    if v_small == v_large:
                        continue
                    n = _decimal_tail_match_len(v_small, v_large)
                    if n < params.min_decimal_match_length:
                        continue
                    d = abs(v_small - v_large)
                    if not _is_round_diff(d, params.decimal_match_max_diff_places):
                        continue
                    if n > best_n:
                        best_n = n
                        best_v = v_large
                        best_diff = d
                if best_v is not None and best_diff is not None:
                    matched.append({
                        "sheet_a_value": normalized_number(v_small),
                        "sheet_b_value": normalized_number(best_v),
                        "decimal_match_length": best_n,
                        "difference": str(best_diff.normalize()),
                    })
                elif v_small in set(larger_vals):
                    # Exact duplicate across sheets — still counts even without
                    # a round-diff match in the suffix bucket.
                    pass

            smaller_total = len(smaller_vals)
            if smaller_total == 0:
                continue
            fraction = len(matched) / smaller_total
            if fraction < params.min_cross_sheet_fraction:
                continue
            if len(matched) < params.min_cross_sheet_matches:
                continue

            max_len = max(m["decimal_match_length"] for m in matched)
            round_count = sum(1 for m in matched if "." not in m["difference"] or len(m["difference"].split(".")[1]) <= 1)
            risk = "critical" if max_len >= 6 and round_count >= 3 else (
                "high" if max_len >= 6 else "medium"
            )
            findings.append({
                "finding_id": None,
                "category": "cross_sheet_decimal_match",
                "risk_level": risk,
                "confidence": "high" if max_len >= 8 else "medium",
                "workbook_a": key_a[0],
                "sheet_a": key_a[1],
                "workbook_b": key_b[0],
                "sheet_b": key_b[1],
                "matched_pairs": len(matched),
                "sheet_a_total": len(vals_a),
                "sheet_b_total": len(vals_b),
                "fraction": round(fraction, 4),
                "max_decimal_match_length": max_len,
                "sample_pairs": sorted(matched, key=lambda m: -m["decimal_match_length"])[:20],
                "benign_explanations": [
                    "两个不同实验系统的独立测量数据共享末位小数位是不正常的。",
                    "如果差异是整数或有限位小数，则数据可能是从共同模板派生或人工生成的。",
                ],
                "pressure_test_result": "needs_cross_sheet_decimal_review",
                "next_steps": [
                    f"对比 {key_a[1]} 和 {key_b[1]} 对应的实验设计，确认是否为完全独立的实验系统。",
                    "如是独立实验，跨图共享高精度末位小数无法用测量误差解释。",
                    "要求提供两个实验的原始仪器导出或独立数据来源证明。",
                ],
            })

    return sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), -item["matched_pairs"]))[
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
        "small_group_fixed_relationship": "SGR",
        "perfect_duplicate_values": "PDV",
        "cross_sheet_decimal_match": "CSD",
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
    small_group_relationships = []
    perfect_duplicates = []
    all_sheets: list[SheetVectors] = []
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
        all_sheets.extend(sheets)
        for sheet in sheets:
            scalar_findings.extend(row_offset_scalar_findings(sheet, params))
            ratio_findings.extend(paired_ratio_reuse_findings(sheet, params))
            duplicate_rows.extend(duplicate_row_vector_findings(sheet, params))
            long_ratio_reuse.extend(long_format_paired_ratio_reuse_findings(sheet, params))
            long_ratio_enrichment.extend(long_format_within_pair_ratio_enrichment_findings(sheet, params))
            rounding_bias.extend(row_offset_rounding_bias_findings(sheet, params))
            small_group_relationships.extend(small_group_fixed_relationship_findings(sheet, params))
            perfect_duplicates.extend(perfect_duplicate_value_findings(sheet, params))

    # Cross-sheet analysis — needs all sheets at once
    cross_sheet_decimal = cross_sheet_decimal_match_findings(all_sheets, params)

    findings = [
        *scalar_findings,
        *ratio_findings,
        *duplicate_rows,
        *long_ratio_reuse,
        *long_ratio_enrichment,
        *rounding_bias,
        *small_group_relationships,
        *perfect_duplicates,
        *cross_sheet_decimal,
    ]
    findings = sorted(findings, key=lambda item: (-risk_rank(item["risk_level"]), str(item.get("workbook")), str(item.get("sheet"))))
    assign_ids(findings)
    priority_findings = [finding for finding in findings if risk_rank(finding.get("risk_level", "")) >= 2]
    by_category = Counter(finding["category"] for finding in findings)
    # Generate hub-sheets summary for cross-sheet relationships
    def generate_hub_sheets_summary(cross_sheet_findings: list[dict[str, Any]]):
        if not cross_sheet_findings:
            return []

        # Build connected components
        components = []
        for finding in cross_sheet_findings:
            node_a = (finding['workbook_a'], finding['sheet_a'])
            node_b = (finding['workbook_b'], finding['sheet_b'])

            comp_a = None
            comp_b = None
            for comp in components:
                if node_a in comp:
                    comp_a = comp
                if node_b in comp:
                    comp_b = comp
                if comp_a and comp_b:
                    break

            if comp_a and comp_b:
                if comp_a is not comp_b:
                    comp_a.update(comp_b)
                    components.remove(comp_b)
            elif comp_a:
                comp_a.add(node_b)
            elif comp_b:
                comp_b.add(node_a)
            else:
                components.append({node_a, node_b})

        # For each component, find the hub node (highest degree)
        hub_summaries = []
        for comp in components:
            if len(comp) <= 1:
                continue

            # Calculate degree within component
            degrees = {node: 0 for node in comp}
            for finding in cross_sheet_findings:
                node_a = (finding['workbook_a'], finding['sheet_a'])
                node_b = (finding['workbook_b'], finding['sheet_b'])
                if node_a in comp and node_b in comp:
                    degrees[node_a] += 1
                    degrees[node_b] += 1

            # Find hub node (highest degree)
            hub_node, _ = max(degrees.items(), key=lambda x: x[1])
            spoke_count = len(comp) - 1

            # Format hub identifier (simplified for readability)
            hub_identifier = f"{hub_node[0]}/{hub_node[1]}"
            hub_summaries.append({
                "hub_sheet": hub_identifier,
                "spoke_sheet_count": spoke_count,
                "spoke_sheets": [f"{n[0]}/{n[1]}" for n in comp if n != hub_node],
                "findings_count": degrees[hub_node]
            })
        return hub_summaries

    hub_sheets_summary = generate_hub_sheets_summary(cross_sheet_decimal)

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
            "min_small_group_size": params.min_small_group_size,
            "max_small_group_size": params.max_small_group_size,
            "min_decimal_match_length": params.min_decimal_match_length,
            "decimal_match_max_diff_places": params.decimal_match_max_diff_places,
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
            "small_group_fixed_relationship_findings": len(small_group_relationships),
            "perfect_duplicate_value_findings": len(perfect_duplicates),
            "cross_sheet_decimal_match_findings": len(cross_sheet_decimal),
            "by_category": dict(by_category),
            "errors": len(errors),
        },
        "hub_sheets_summary": hub_sheets_summary,
        "findings": findings,
        "priority_findings": priority_findings,
        "row_offset_scalar_findings": scalar_findings,
        "paired_ratio_reuse_findings": ratio_findings,
        "duplicate_row_vector_findings": duplicate_rows,
        "long_format_paired_ratio_reuse_findings": long_ratio_reuse,
        "long_format_within_pair_ratio_enrichment_findings": long_ratio_enrichment,
        "rounding_bias_findings": rounding_bias,
        "small_group_fixed_relationship_findings": small_group_relationships,
        "perfect_duplicate_value_findings": perfect_duplicates,
        "cross_sheet_decimal_match_findings": cross_sheet_decimal,
        "errors": errors,
        "limitations": [
            "该工具只识别 XLSX 中的通用行偏移、配对比例复用、long-format 成对比例复用和低宽度行重复模式，不判断最终科研诚信。",
            "行是否代表独立样本、患者或技术重复需要结合 sheet 注释、论文方法和原始仪器输出人工确认。",
            "ratio_places 会影响 paired ratio reuse 的敏感度；高精度与展示值四舍五入场景应分开解释。",
            "低信息数值列会被视为分组/类别/编号候选并排除在连续测量列检测之外，可能降低二分类测量场景的敏感度。",
            "小样本组检测 (small_group/perfect_duplicate) 针对 2-7 行的生物学重复设计，高精度数值匹配下假阳性率较低。",
            "跨 sheet 小数匹配检测 (cross_sheet_decimal_match) 仅比较不同 sheet 间的数值对，不判断 sheet 内部关系。",
            "hub_sheets_summary 提供跨 sheet 复用关系的聚合视图，标识中心 hub sheet 和 spoke 数量。",
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
    parser.add_argument("--min-small-group-size", type=int, default=2, help="Minimum pairs for small-group fixed-relationship detection.")
    parser.add_argument("--max-small-group-size", type=int, default=7, help="Columns with ≤ this many values are eligible for small-group detectors.")
    parser.add_argument("--min-decimal-match-length", type=int, default=6, help="Minimum trailing decimal digits that must match in cross-sheet comparison.")
    parser.add_argument("--decimal-match-max-diff-places", type=int, default=1, help="Max significant decimal places for a 'round' difference in cross-sheet matching.")
    parser.add_argument("--min-cross-sheet-fraction", type=float, default=0.50, help="Minimum fraction of values that must match between two sheets to emit a cross-sheet finding.")
    parser.add_argument("--min-cross-sheet-matches", type=int, default=6, help="Minimum number of matched value pairs required for a cross-sheet finding.")
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
        min_small_group_size=max(2, args.min_small_group_size),
        max_small_group_size=max(2, args.max_small_group_size),
        min_decimal_match_length=max(2, args.min_decimal_match_length),
        decimal_match_max_diff_places=max(0, args.decimal_match_max_diff_places),
        min_cross_sheet_fraction=min(1.0, max(0.0, args.min_cross_sheet_fraction)),
        min_cross_sheet_matches=max(2, args.min_cross_sheet_matches),
    )
    result = analyze_xlsx_root(xlsx_root, params)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
