"""R10: 数据泄露检测 —— 训练/测试分割独立性。

造假模式：训练集和测试集未严格分离，或特征筛选在分割前进行，
导致虚高模型性能（AUC/精度膨胀）。

输入：分析代码文件或代码目录
输出：CheckResult
"""

import re
from pathlib import Path
from typing import Optional

from .base import BaseChecker, CheckResult, Finding, Status


class R10DataLeakageChecker(BaseChecker):
    rule_id = "R10"
    rule_name = "数据泄露检测"
    input_kind = "code"

    # R 中 train/test split 的模式
    TRAIN_TEST_R = {
        "createDataPartition", "sample.split", "train_test_split",
        "initial_split", "training", "testing", "createFolds",
        "groupKFold", "group_k_fold",
    }

    # Python 中 train/test split 的模式
    TRAIN_TEST_PY = {
        "train_test_split", "KFold", "StratifiedKFold",
        "GroupKFold", "train_test_split", "cross_val",
    }

    # 危险模式：在 split 前做全局数据处理
    DANGER_PATTERNS = [
        # 在 split 前做 scaling
        (r"(scale|standardize|normalize|StandardScaler|MinMaxScaler)\s*\(?\s*(?:.*?data|.*?X|.*?df)", "split 前"),
        # 在 split 前做特征选择
        (r"(SelectKBest|feature_importance|variancethreshold|select_features)\s*\(?\s*(?:.*?data|.*?X)", "split 前"),
        # 在 split 前做 PCA
        (r"(pca|PCA)\s*\.?\s*(?:fit|fit_transform)\s*\(\s*(?:.*?data|.*?X)", "split 前"),
        # 全局缺失值填充
        (r"(fillna|impute|SimpleImputer|complete\.cases)\s*\(?\s*(?:.*?data|.*?df)", "split 前"),
    ]

    def check(self, data_path: str, metadata: Optional[dict] = None) -> CheckResult:
        result = CheckResult(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status=Status.PASS,
        )

        code_files = _collect_code_files(data_path)
        if not code_files:
            result.status = Status.WARN
            result.findings.append(Finding(
                description=f"未找到代码文件（.py/.R/.Rmd/.ipynb）：{data_path}",
                location=data_path,
                severity="low",
            ))
            return result

        result.metadata["n_files"] = len(code_files)

        for fpath in code_files:
            try:
                with open(fpath) as f:
                    content = f.read()
            except Exception:
                continue

            ext = Path(fpath).suffix.lower()
            is_r = ext in {".r", ".rmd"}
            is_py = ext in {".py", ".ipynb"}

            # 检查 1: 是否有 train/test split
            has_split = False
            if is_r:
                has_split = any(p in content.lower() for p in self.TRAIN_TEST_R)
            elif is_py:
                has_split = any(p in content for p in self.TRAIN_TEST_PY)

            if not has_split:
                # 可能代码没有显式 split，或者 split 在其他文件
                continue

            # 检查 2: 危险模式 —— 在 split 前做全局预处理
            split_pattern = r"(train_test_split|createDataPartition|initial_split)"
            split_lines = []
            for i, line in enumerate(content.split("\n"), 1):
                if re.search(split_pattern, line):
                    split_lines.append(i)

            for pattern, desc in self.DANGER_PATTERNS:
                for m in re.finditer(pattern, content, re.IGNORECASE):
                    line_no = content[:m.start()].count("\n") + 1
                    # 检查这个操作是否在 split 之前
                    if split_lines and line_no < min(split_lines):
                        result.status = Status.FAIL
                        result.findings.append(Finding(
                            description=f"数据泄露风险：{desc}预处理 ({m.group(0)[:60]}) 在 train/test 分割之前执行。",
                            location=f"{fpath.name}:L{line_no}",
                            value=m.group(0)[:80],
                            expected="预处理应在 split 之后，仅对训练集操作",
                            severity="high",
                        ))
                    elif not split_lines:
                        # split 不在同一文件，无法判断先后
                        result.status = Status.WARN
                        result.findings.append(Finding(
                            description=f"潜在数据泄露风险：{desc}预处理 ({m.group(0)[:60]})，" +
                                         "无法确认是否在 train/test 分割之后执行。",
                            location=f"{fpath.name}:L{line_no}",
                            value=m.group(0)[:80],
                            expected="确认仅对训练集操作",
                            severity="medium",
                        ))

        return self._summarize(result)


def _collect_code_files(path: str) -> list:
    """收集目录下所有代码文件"""
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in {".py", ".r", ".rmd", ".ipynb"}:
            return [p]
        return []

    code_files = []
    for ext in ["*.py", "*.R", "*.Rmd", "*.ipynb"]:
        code_files.extend(p.rglob(ext))
    return sorted(set(code_files))
