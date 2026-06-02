"""R4: 重复列检测 —— 检测技术重复冒充生物学重复。

造假模式：表达谱中多列数值近乎完美重合（r > 0.99），
将技术重复数据混洗后当生物学重复使用。

输入：表达矩阵（TSV/CSV/mtx，行=基因，列=样本）
输出：CheckResult
"""

import re
import numpy as np
from pathlib import Path
from typing import Optional
from scipy.stats import pearsonr

from .base import BaseChecker, CheckResult, Finding, Status


THRESHOLD = 0.99          # 相关系数阈值
MIN_EXPR_GENES = 500      # 最少表达基因数（过滤低质量样本）


class R4DuplicateColumnsChecker(BaseChecker):
    rule_id = "R4"
    rule_name = "重复列检测"
    input_kind = "matrix"

    def check(self, data_path: str, metadata: Optional[dict] = None) -> CheckResult:
        result = CheckResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status=Status.PASS,
        )

        # 自动识别文件格式并加载
        try:
            matrix, col_names = _load_matrix(data_path)
        except Exception as e:
            result.status = Status.WARN
            result.findings.append(Finding(
                description=f"数据加载失败: {e}",
                location=data_path,
            ))
            return self._summarize(result)

        if matrix is None or matrix.shape[1] < 2:
            result.status = Status.WARN
            result.findings.append(Finding(
                description="样本数不足，无法进行重复列检测",
                location=data_path,
            ))
            return self._summarize(result)

        n_samples = matrix.shape[1]
        result.metadata["n_samples"] = n_samples
        result.metadata["n_genes"] = matrix.shape[0]

        # 过滤低表达样本
        expressed = (matrix > 0).sum(axis=0)
        valid_mask = expressed >= MIN_EXPR_GENES
        valid_names = [cn for cn, v in zip(col_names, valid_mask) if v]
        matrix = matrix[:, valid_mask]

        if matrix.shape[1] < 2:
            return self._summarize(result)

        # 计算样本间相关系数矩阵
        corr = np.corrcoef(matrix, rowvar=False)

        # 查找上三角中 r > THRESHOLD 的列对
        n = corr.shape[0]
        for i in range(n):
            for j in range(i + 1, n):
                r = corr[i, j]
                if abs(r) >= THRESHOLD:
                    # 确认不是转录组学中天然的极高相关（如 technical reps）
                    result.findings.append(Finding(
                        description=f"样本对相关系数异常高 (r={r:.4f})，疑似技术重复冒充生物学重复",
                        location=f"{valid_names[i]} ↔ {valid_names[j]}",
                        value=round(float(r), 4),
                        expected=f"|r| < {THRESHOLD}",
                        severity="high",
                    ))

        if result.findings:
            result.status = Status.FAIL

        return self._summarize(result)


def _load_matrix(path: str):
    """自动识别格式：.mtx / .tsv / .csv / .txt"""
    p = Path(path)

    # 处理 Matrix Market 格式 (.mtx)
    if p.suffix == ".mtx":
        return _load_mtx(path)

    # 处理 TSV/CSV/TXT
    ext_map = {".tsv": "\t", ".csv": ",", ".txt": "\t", ".gz": "\t"}
    sep = ext_map.get(p.suffix, None)
    if sep is None:
        # 尝试用后缀判定
        name = p.name.lower()
        if name.endswith(".tsv.gz") or name.endswith(".tsv"):
            sep = "\t"
        elif name.endswith(".csv.gz") or name.endswith(".csv"):
            sep = ","
        else:
            sep = "\t"

    try:
        import pandas as pd
        df = pd.read_csv(path, sep=sep, index_col=0)
        return df.values.T.astype(float), list(df.columns)
    except Exception:
        # 降级：手动解析
        data, headers, _ = _parse_genex_matrix(path, sep)
        return data, headers


def _load_mtx(path: str):
    """加载 Matrix Market 格式。需要同目录下的 genes.tsv 和 barcodes.tsv"""
    p = Path(path)
    parent = p.parent
    base = p.stem.replace(".counts", "").replace(".mtx", "")

    # 去除 .mtx 后缀 → 找对应的 genes/barcodes 文件
    stem = p.name
    for suffix in [".counts.mtx", ".mtx"]:
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break

    # 找 genes 文件
    genes_file = None
    for cand in [
        parent / f"{stem}.genes.tsv",
        parent / f"{stem}_genes.tsv",
        parent / "genes.tsv",
    ]:
        if cand.exists():
            genes_file = cand
            break

    # 找 barcodes 文件
    barcodes_file = None
    for cand in [
        parent / f"{stem}.barcodes.tsv",
        parent / f"{stem}_barcodes.tsv",
        parent / "barcodes.tsv",
    ]:
        if cand.exists():
            barcodes_file = cand
            break

    # 解析 mtx
    with open(path) as f:
        # 跳过注释行
        line = f.readline()
        while line.startswith("%"):
            line = f.readline()
        # 读取维度
        parts = line.strip().split()
        n_rows, n_cols, n_entries = int(parts[0]), int(parts[1]), int(parts[2])

        # 构建稀疏矩阵
        from scipy.sparse import coo_matrix
        row_idx, col_idx, values = [], [], []
        for line in f:
            if not line.strip():
                continue
            r, c, v = line.strip().split()
            row_idx.append(int(r) - 1)  # mtx 是 1-indexed
            col_idx.append(int(c) - 1)
            values.append(float(v))

    matrix = coo_matrix((values, (row_idx, col_idx)), shape=(n_rows, n_cols)).toarray()

    # 加载列名
    col_names = [f"sample_{i}" for i in range(n_cols)]
    if barcodes_file and barcodes_file.exists():
        with open(barcodes_file) as f:
            col_names = [line.strip().split("\t")[0] for line in f if line.strip()]

    return matrix, col_names


def _parse_genex_matrix(path: str, sep: str):
    """通用表达矩阵解析"""
    with open(path) as f:
        lines = f.readlines()

    headers = lines[0].strip().split(sep)
    data = []
    for line in lines[1:]:
        parts = line.strip().split(sep)
        if len(parts) >= 2:
            data.append([float(x) if _is_float(x) else 0.0 for x in parts[1:]])

    return np.array(data).T, headers[1:]


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
