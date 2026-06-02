"""MedgeBench 论文质控检测器包。

每个 checker 继承 BaseChecker，实现 check(data_path, metadata) → CheckResult。

用法：
    from knowledge_base.checkers import get_checker, list_checkers

    # 列出所有检测器
    for c in list_checkers():
        print(c.rule_id, c.rule_name)

    # 运行单个检测器
    checker = get_checker("R4")
    result = checker.check("path/to/counts.mtx")
    print(result.to_dict())
"""

from .base import BaseChecker, CheckResult, Finding, Status
from .r4_duplicate_columns import R4DuplicateColumnsChecker
from .r5_numeric_anomaly import R5NumericAnomalyChecker
from .r1_pseudoreplication import R1PseudoreplicationChecker
from .r2_batch_confounding import R2BatchConfoundingChecker
from .r10_data_leakage import R10DataLeakageChecker
from .r11_label_leakage import R11LabelLeakageChecker

# 已实现的检测器注册表
_CHECKERS = {
    "R1": R1PseudoreplicationChecker,
    "R2": R2BatchConfoundingChecker,
    "R4": R4DuplicateColumnsChecker,
    "R5": R5NumericAnomalyChecker,
    "R10": R10DataLeakageChecker,
    "R11": R11LabelLeakageChecker,
}

# 待实现（需要更多前置调研或依赖平台接口）
# R3:  统计预筛选偏差 — 需要分析代码 + 检测 fold-change 预筛选
# R6:  缺失值模式 — 需要统计学检验
# R7:  去污染步骤 — 需要微生物组领域知识
# R8:  批次校正必要性 — 与 R2 配合
# R9:  效应量-结论匹配 — 需要 LLM/NLP
# R12: 代码可执行性 — 需要 Docker 沙箱


def get_checker(rule_id: str) -> BaseChecker:
    """根据规则 ID 获取检测器实例"""
    cls = _CHECKERS.get(rule_id.upper())
    if cls is None:
        raise ValueError(f"未实现的检测器: {rule_id}。可用: {list(_CHECKERS.keys())}")
    return cls()


def list_checkers():
    """列出所有已实现的检测器"""
    return [(rid, cls.rule_name, cls.input_kind)
            for rid, cls in _CHECKERS.items()]


__all__ = [
    "BaseChecker", "CheckResult", "Finding", "Status",
    "get_checker", "list_checkers",
]
