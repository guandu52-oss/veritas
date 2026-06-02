"""R5: 数值异常检测 —— 检查数据完整性。

造假模式：
- 整数型数据（raw counts）混入非整数值
- p-value 分布异常（U 型/台阶型）
- 不合理零值或负值

输入：数据文件路径
输出：CheckResult
"""

import re
import numpy as np
from pathlib import Path
from typing import Optional

from .base import BaseChecker, CheckResult, Finding, Status


class R5NumericAnomalyChecker(BaseChecker):
    rule_id = "R5"
    rule_name = "数值异常检测"
    input_kind = "matrix"

    def check(self, data_path: str, metadata: Optional[dict] = None) -> CheckResult:
        result = CheckResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status=Status.PASS,
        )

        try:
            data, names = _load_numeric_data(data_path)
        except Exception as e:
            result.status = Status.WARN
            result.findings.append(Finding(
                description=f"数据加载失败: {e}",
                location=data_path,
            ))
            return self._summarize(result)

        if data is None or data.size == 0:
            return self._summarize(result)

        result.metadata["shape"] = list(data.shape)

        # ── 检查 1: 整数类型检查（针对 count data） ──
        _check_integer(data, result)

        # ── 检查 2: 负值检查 ──
        _check_negative(data, result)

        # ── 检查 3: 全零行/列 ──
        _check_all_zero(data, names, result)

        # ── 检查 4: p-value 列分布（如果列名含 p-value/pval/padj） ──
        _check_pvalue_distribution(data, names, result)

        # ── 检查 5: 异常大值/小值 ──
        _check_extreme_values(data, names, result)

        if result.findings:
            # 区分严重程度：high = 数据伪造可能性大
            highs = [f for f in result.findings if f.severity == "high"]
            result.status = Status.FAIL if highs else Status.WARN

        return self._summarize(result)


def _load_numeric_data(path: str):
    """加载数值矩阵。返回 (2D array, column names)"""
    p = Path(path)
    sep = "\t"
    if p.suffix == ".csv":
        sep = ","

    try:
        import pandas as pd
        df = pd.read_csv(path, sep=sep, index_col=0)
        return df.values.astype(float), list(df.columns)
    except ImportError:
        pass

    # 手动解析
    with open(path) as f:
        lines = f.readlines()
    if not lines:
        return None, []

    headers = lines[0].strip().split(sep)
    rows = []
    for line in lines[1:]:
        parts = line.strip().split(sep)
        try:
            rows.append([float(x) for x in parts])
        except ValueError:
            rows.append([float(x) if _try_float(x) else np.nan for x in parts])

    return np.array(rows), headers


def _check_integer(data: np.ndarray, result: CheckResult):
    """检查：如果数据看起来像整数 counts（99% 的值为整数），则标记非整数异常"""
    # 统计整数比例
    rounded = np.round(data)
    within_tol = np.isclose(data, rounded, atol=1e-6)
    int_ratio = within_tol.sum() / data.size

    if int_ratio > 0.99:
        # 很可能是整数 counts
        non_int_mask = ~within_tol
        if non_int_mask.any():
            non_int_count = non_int_mask.sum()
            non_int_ratio = non_int_count / data.size
            result.findings.append(Finding(
                description=f"疑似整数型数据（counts）中含 {non_int_count} 个非整数值 ({non_int_ratio:.4%})。" +
                             "raw counts 不应出现小数。",
                location="整个矩阵",
                value=round(float(non_int_ratio), 4),
                expected="0% 非整数",
                severity="high" if non_int_ratio > 0.01 else "medium",
            ))


def _check_negative(data: np.ndarray, result: CheckResult):
    """检查负值"""
    neg_mask = data < 0
    if neg_mask.any():
        neg_count = neg_mask.sum()
        neg_ratio = neg_count / data.size
        # 找到负值的坐标
        rows, cols = np.where(neg_mask)
        examples = [(int(rows[i]), int(cols[i]), float(data[rows[i], cols[i]])) for i in range(min(5, len(rows)))]
        result.findings.append(Finding(
            description=f"发现 {neg_count} 个负值 ({neg_ratio:.4%})。示例坐标(行,列,值): {examples}",
            location="整个矩阵",
            value=round(float(neg_ratio), 4),
            expected="0 负值",
            severity="medium",
        ))


def _check_all_zero(data: np.ndarray, names: list, result: CheckResult):
    """检查全零行或全零列"""
    # 全零列（样本）
    col_sums = np.sum(np.abs(data), axis=0)
    zero_cols = np.where(col_sums == 0)[0]
    if len(zero_cols) > 0:
        example = [names[i] if i < len(names) else f"col_{i}" for i in zero_cols[:5]]
        result.findings.append(Finding(
            description=f"发现 {len(zero_cols)} 个全零样本列: {example}",
            location=", ".join(example) if len(example) <= 5 else f"{len(zero_cols)} columns",
            value=len(zero_cols),
            expected="0",
            severity="low",
        ))

    # 全零行（基因）
    row_sums = np.sum(np.abs(data), axis=1)
    zero_rows = np.where(row_sums == 0)[0]
    if len(zero_rows) > 0:
        result.findings.append(Finding(
            description=f"发现 {len(zero_rows)} 个全零基因行",
            location=f"共 {len(zero_rows)} 行",
            value=len(zero_rows),
            expected="0",
            severity="low",
        ))


def _check_pvalue_distribution(data: np.ndarray, names: list, result: CheckResult):
    """检查 p-value 列的分布"""
    if not names:
        return

    pval_cols = []
    for i, name in enumerate(names):
        nl = name.lower().replace('"', '').replace("'", '')
        if any(kw in nl for kw in ['pval', 'p_val', 'p.val', 'p-value', 'padj', 'qval', 'q.val']):
            pval_cols.append(i)

    for i in pval_cols:
        col = data[:, i]
        col = col[~np.isnan(col)]
        col = col[(col >= 0) & (col <= 1)]
        if len(col) < 20:
            continue

        # 直方图检查分布
        hist, edges = np.histogram(col, bins=20, range=(0, 1))
        hist = hist / hist.sum()

        # 正常 p-value 应该是 [0,1] 均匀或右偏
        # 异常模式：
        # 1. 集中在某个区间 → 台阶型
        max_bin = hist.max()
        if max_bin > 0.3:
            result.findings.append(Finding(
                description=f"p-value 列 '{names[i]}' 分布异常集中 (最大密度={max_bin:.2f})，" +
                             "不符合均匀分布预期，可能经过过滤或操纵。",
                location=names[i],
                value=round(float(max_bin), 2),
                expected="密度 ≤ 0.3",
                severity="high" if max_bin > 0.5 else "medium",
            ))

        # 2. U 型分布检测（两端多中间少）
        edges_bin_ratio = (hist[:3].sum() + hist[-3:].sum()) / hist.sum()
        if edges_bin_ratio < 0.15 and col.mean() > 0.5:
            result.findings.append(Finding(
                description=f"p-value 列 '{names[i]}' 可能被截断（均值={col.mean():.3f}，两端密度={edges_bin_ratio:.2f}）",
                location=names[i],
                value=round(float(col.mean()), 3),
                expected="均匀分布均值 ≈ 0.5",
                severity="high",
            ))


def _check_extreme_values(data: np.ndarray, names: list, result: CheckResult):
    """检查异常大值或小值（Z-score 判定）"""
    # 跳过全零列
    stds = np.std(data, axis=0)
    valid_cols = stds > 0
    if valid_cols.sum() == 0:
        return

    data_sub = data[:, valid_cols]
    valid_names = [names[i] for i in range(len(names)) if valid_cols[i]]

    # 每列 Z-score
    means = np.mean(data_sub, axis=0)
    z_scores = np.abs((data_sub - means) / stds[valid_cols])

    extreme_mask = z_scores > 10
    extreme_count = extreme_mask.sum()
    if extreme_count > 0:
        extreme_ratio = extreme_count / extreme_mask.size
        result.findings.append(Finding(
            description=f"发现 {extreme_count} 个极端值 (|Z| > 10, 占 {extreme_ratio:.4%})",
            location="整个矩阵",
            value=round(float(extreme_ratio), 4),
            expected="< 0.01%",
            severity="low",
        ))


def _try_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
