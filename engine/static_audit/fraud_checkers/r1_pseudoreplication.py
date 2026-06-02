"""R1: 伪重复检测。

造假模式：将同一动物的多个细胞视为独立样本，未使用
pseudobulk 或混合效应模型处理嵌套数据结构。

输入：表达矩阵 + metadata（样本注释信息）
输出：CheckResult

metadata 示例：
{
  "sample_annotations": {
    "sample_id": ["s1", "s2", "s3", ...],
    "subject_id":  ["m1", "m1", "m2", ...],   # 哪些样本来自同一动物
    "condition":   ["ctrl", "ctrl", "treat", ...],
  },
  "method": "DESeq2",       # 使用的分析方法
  "nested_design": True,     # 是否存在嵌套结构（由前置分析判断）
}
"""

import numpy as np
from collections import Counter
from typing import Optional

from .base import BaseChecker, CheckResult, Finding, Status


class R1PseudoreplicationChecker(BaseChecker):
    rule_id = "R1"
    rule_name = "伪重复检测"
    input_kind = "metadata"

    # 支持嵌套设计的方法
    NESTED_METHODS = {
        "pseudobulk", "lmer", "lme4", "glmer", "glmm", "lmm",
        "mixed_effects", "mixed.model", "mixed effects model",
        "dream", "variancePartition", "limma_duplicateCorrelation",
        "aggregate", "muscat", "milo", "nebula",
    }

    # 不支持嵌套的方法（将细胞当独立样本）
    FLAT_METHODS = {
        "t.test", "ttest", "t-test", "wilcoxon", "wilcox",
        "deseq2", "deseq", "edger", "limma",
        "logistic regression", "glm",
    }

    def check(self, data_path: str, metadata: Optional[dict] = None) -> CheckResult:
        result = CheckResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status=Status.PASS,
            findings=[],
        )

        if not metadata:
            result.status = Status.WARN
            result.findings.append(Finding(
                description="缺少样本注释 metadata，无法进行伪重复检测。需提供 sample_annotations 和 method 字段。",
                location="metadata",
                severity="low",
            ))
            return result

        annotations = metadata.get("sample_annotations", {})
        method = metadata.get("method", "").lower()
        nested_design = metadata.get("nested_design", None)

        # 如果没有显式标注嵌套设计，尝试从 annotations 推断
        if nested_design is None and annotations:
            nested_design = _detect_nested_design(annotations)

        result.metadata["nested_design_detected"] = nested_design
        result.metadata["method"] = method

        if not nested_design:
            # 无嵌套结构，通过
            return self._summarize(result)

        # 有嵌套结构 → 检查方法是否兼容
        method_is_nested = any(m in method for m in self.NESTED_METHODS)
        method_is_flat = any(m in method for m in self.FLAT_METHODS)

        if method_is_flat and not method_is_nested:
            # 嵌套数据 + 扁平方法 = 伪重复
            subject_counts = Counter(
                annotations.get("subject_id", [])
            )
            multi_cell_subjects = sum(1 for v in subject_counts.values() if v > 1)

            result.status = Status.FAIL
            result.findings.append(Finding(
                description=(
                    f"伪重复风险：数据存在嵌套结构（{multi_cell_subjects} 个 subject 有多个细胞/样本），"
                    f"但使用了不支持嵌套设计的分析方法 ({method})。"
                    f"应将同 subject 的多个细胞聚合为 pseudobulk，或使用混合效应模型。"
                ),
                location="分析方法选择",
                value=method,
                expected="pseudobulk / mixed-effects model",
                severity="high",
            ))
            result.findings.append(Finding(
                description=(
                    f"嵌套结构详情：{len(subject_counts)} 个 subjects，"
                    f"其中 {multi_cell_subjects}/{len(subject_counts)} 有 ≥2 个观察值"
                ),
                location="sample_annotations",
                value=dict(subject_counts.most_common(5)),
                expected="每个 subject 1 个观察值（或使用嵌套方法）",
                severity="medium",
            ))

        return self._summarize(result)


def _detect_nested_design(annotations: dict) -> bool:
    """从样本注释推断是否存在嵌套结构"""
    subject_id = annotations.get("subject_id", [])
    sample_id = annotations.get("sample_id", [])

    if not subject_id or not sample_id:
        return False

    # 如果一个 subject 对应多个 sample → 嵌套
    counts = Counter(subject_id)
    total_subjects = len(counts)
    multi_sample_subjects = sum(1 for v in counts.values() if v > 1)

    return multi_sample_subjects > 0
