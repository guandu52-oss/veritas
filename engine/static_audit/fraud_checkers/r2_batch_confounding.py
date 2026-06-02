"""R2: 批次混淆检测。

造假模式：实验批次变量（lane/date/operator）与生物学变量
（condition/species/treatment）完全共线，使得批次效应无法与
生物学效应区分。

输入：metadata（包含样本注释和批次信息）
输出：CheckResult

metadata 示例：
{
  "sample_annotations": {
    "sample_id":    ["s1", "s2", "s3", "s4"],
    "batch":        ["lane1", "lane1", "lane2", "lane2"],
    "condition":    ["human", "human", "mouse", "mouse"],
  },
  "batch_correction": false,   # 是否进行了批次校正
  "batch_correction_method": null,
}
"""

import numpy as np
from typing import Optional
from collections import Counter

from .base import BaseChecker, CheckResult, Finding, Status


class R2BatchConfoundingChecker(BaseChecker):
    rule_id = "R2"
    rule_name = "批次混淆检测"
    input_kind = "metadata"

    ASSOCIATION_THRESHOLD = 0.9   # Cramér's V 阈值

    # 批次校正方法
    BATCH_CORRECTION_METHODS = {
        "combat", "combar_seq", "harmony", "scanorama", "mnn",
        "seurat", "cca", "limma_removebatcheffect", "ruv",
        "liger", "fastmnn", "bbknn", "scvi",
    }

    def check(self, data_path: str, metadata: Optional[dict] = None) -> CheckResult:
        result = CheckResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status=Status.PASS,
        )

        if not metadata:
            result.status = Status.WARN
            result.findings.append(Finding(
                description="缺少 metadata，无法检测批次混淆。需提供 sample_annotations。",
                location="metadata",
                severity="low",
            ))
            return result

        if "sample_annotations" not in metadata:
            result.status = Status.WARN
            result.findings.append(Finding(
                description="metadata 缺少 sample_annotations 字段",
                location="metadata",
                severity="low",
            ))
            return result

        df = metadata["sample_annotations"]
        batch_vars = _find_batch_variables(df)
        bio_vars = _find_biological_variables(df)

        if not batch_vars:
            result.findings.append(Finding(
                description="未找到批次变量（batch/lane/date/plate 等）。若存在批次但未标注，标记为信息缺失。",
                location="sample_annotations",
                severity="low",
            ))
            return self._summarize(result)

        if not bio_vars:
            result.findings.append(Finding(
                description="未找到生物学变量（condition/treatment/group 等）",
                location="sample_annotations",
                severity="low",
            ))
            return self._summarize(result)

        result.metadata["batch_vars"] = batch_vars
        result.metadata["bio_vars"] = bio_vars

        # 检查每对 (batch_var, bio_var) 的共线性
        n_samples = len(list(df.values())[0])
        for batch_var in batch_vars:
            for bio_var in bio_vars:
                v_score = _cramers_v(
                    _categorize(df[batch_var]),
                    _categorize(df[bio_var]),
                )
                if v_score > self.ASSOCIATION_THRESHOLD:
                    result.status = Status.FAIL
                    result.findings.append(Finding(
                        description=(
                            f"严重批次混淆：{batch_var} 与 {bio_var} 高度共线 (Cramér's V={v_score:.3f})。"
                            f"批次效应无法与生物学效应分离。"
                        ),
                        location=f"{batch_var} ↔ {bio_var}",
                        value=round(v_score, 3),
                        expected=f"Cramér's V < {self.ASSOCIATION_THRESHOLD}",
                        severity="high",
                    ))
                elif v_score > 0.5:
                    result.status = Status.WARN
                    result.findings.append(Finding(
                        description=(
                            f"{batch_var} 与 {bio_var} 中度相关 (Cramér's V={v_score:.3f})，"
                            f"建议进行批次校正并可视化验证。"
                        ),
                        location=f"{batch_var} ↔ {bio_var}",
                        value=round(v_score, 3),
                        expected=f"Cramér's V < 0.5",
                        severity="medium",
                    ))

        # 检查是否进行了批次校正
        batch_corrected = metadata.get("batch_correction", False)
        method = metadata.get("batch_correction_method", "").lower()

        has_correction = batch_corrected or any(
            m in method for m in self.BATCH_CORRECTION_METHODS
        )

        if result.status in (Status.FAIL, Status.WARN) and not has_correction:
            result.findings.append(Finding(
                description="存在批次混淆但未见批次校正步骤。建议使用 ComBat / Harmony / Seurat CCA。",
                location="分析流程",
                value="未校正",
                expected="ComBat / Harmony / Seurat CCA",
                severity="high" if result.status == Status.FAIL else "medium",
            ))

        return self._summarize(result)


def _find_batch_variables(df: dict) -> list:
    """从列名中识别批次变量"""
    batch_keys = {"batch", "lane", "plate", "date", "run", "operator",
                  "sequencer", "prep", "library", "chip", "array"}
    found = []
    for key in df.keys():
        key_lower = key.lower().replace("_", "").replace("-", "").replace(" ", "")
        if any(bk in key_lower for bk in batch_keys):
            found.append(key)
    return found


def _find_biological_variables(df: dict) -> list:
    """识别生物学变量（非批次/非ID/非技术变量）"""
    skip = {"sample_id", "sample", "id", "barcode", "index",
            "batch", "lane", "plate", "date", "run", "operator",
            "sequencer", "prep", "library", "chip", "array",
            "file", "path", "filename", "replicate", "rep"}
    found = []
    for key in df.keys():
        key_lower = key.lower()
        if not any(sk in key_lower for sk in skip):
            found.append(key)
    return found if found else list(df.keys())


def _categorize(values: list) -> list:
    """将值转为离散类别"""
    unique_vals = list(set(values))
    mapping = {v: i for i, v in enumerate(unique_vals)}
    return [mapping[v] for v in values]


def _cramers_v(x: list, y: list) -> float:
    """计算两个分类变量之间的 Cramér's V 关联度"""
    n = len(x)
    if n == 0:
        return 0.0

    # 构建列联表
    x_vals = sorted(set(x))
    y_vals = sorted(set(y))
    x_map = {v: i for i, v in enumerate(x_vals)}
    y_map = {v: i for i, v in enumerate(y_vals)}

    table = np.zeros((len(x_vals), len(y_vals)))
    for xi, yi in zip(x, y, strict=False):
        table[x_map[xi], y_map[yi]] += 1

    # χ² 检验
    row_sums = table.sum(axis=1, keepdims=True)
    col_sums = table.sum(axis=0, keepdims=True)
    expected = row_sums @ col_sums / n
    # 避免除零
    mask = expected > 0
    chi2 = np.sum((table[mask] - expected[mask]) ** 2 / expected[mask])

    # Cramér's V
    k = min(len(x_vals), len(y_vals))
    if k <= 1 or n == 0:
        return 0.0
    return float(np.sqrt(chi2 / (n * (k - 1))))
