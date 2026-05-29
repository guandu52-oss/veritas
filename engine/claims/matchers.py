from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from engine.reporting.models import Finding


def load_result_map(path: Path) -> dict[tuple[str, str], str]:
    data: dict[tuple[str, str], str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dataset = (row.get("dataset") or "").strip()
            metric = (row.get("metric") or "").strip()
            value = (row.get("value") or "").strip()
            if dataset and metric and value:
                data[(dataset, metric)] = value
    return data


def compare_claims(
    claims: list[dict[str, Any]], result_map: dict[tuple[str, str], str]
) -> tuple[list[dict[str, Any]], list[Finding]]:
    rows: list[dict[str, Any]] = []
    findings: list[Finding] = []

    for index, claim in enumerate(claims, start=1):
        claim_id = claim.get("id", f"C-{index:03d}")
        dataset = str(claim.get("dataset", "")).strip()
        metric = str(claim.get("metric", "")).strip()
        expected_raw = str(claim.get("expected", "")).strip()
        actual_raw = result_map.get((dataset, metric))
        source = str(claim.get("source", "claim"))
        tolerance = float(claim.get("tolerance", 0.0))

        row = {
            "id": claim_id,
            "source": source,
            "dataset": dataset,
            "metric": metric,
            "expected": expected_raw,
            "actual": actual_raw,
            "status": "matched",
        }

        if actual_raw is None:
            row["status"] = "missing"
            findings.append(
                Finding(
                    id=f"F-{100 + index:03d}",
                    title=f"缺少结果证据: {dataset} / {metric}",
                    severity="warning",
                    category="numerical",
                    status="open",
                    fact=f"{source} 声明 {dataset} 的 {metric} 为 {expected_raw}，但 results 文件中没有对应记录。",
                    inference="当前无法判断是结果文件不完整，还是论文 claim 缺乏可追溯证据。",
                    suggestion="补充结构化结果导出，或在论文中移除无法追溯的数字 claim。",
                    source=source,
                )
            )
            rows.append(row)
            continue

        expected = _to_float(expected_raw)
        actual = _to_float(actual_raw)
        if expected is None or actual is None:
            row["status"] = "unparsed"
            findings.append(
                Finding(
                    id=f"F-{100 + index:03d}",
                    title=f"无法解析数值: {dataset} / {metric}",
                    severity="warning",
                    category="numerical",
                    status="open",
                    fact=f"Expected={expected_raw}, actual={actual_raw}，至少一方不是可解析浮点数。",
                    inference="数字一致性检查依赖结构化数值；当前 claim 或 results 格式不规范。",
                    suggestion="统一导出纯数值字段，例如 CSV 中仅保留数值本身。",
                    source=source,
                )
            )
            rows.append(row)
            continue

        diff = abs(expected - actual)
        row["difference"] = round(diff, 6)
        if diff > tolerance:
            row["status"] = "mismatched"
            findings.append(
                Finding(
                    id=f"F-{100 + index:03d}",
                    title=f"数字不一致: {dataset} / {metric}",
                    severity="critical",
                    category="numerical",
                    status="open",
                    fact=f"{source} 声明值为 {expected_raw}，results 中实际值为 {actual_raw}，差值 {diff:.6f}。",
                    inference="高概率是论文数字未同步、引用错行，或结果文件版本与稿件版本不一致。",
                    suggestion="核对 claim 来源，必要时同步修正文稿和结果文件；修改后重新生成报告。",
                    source=source,
                    rerun_required=True,
                )
            )
        rows.append(row)

    return rows, findings


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
