"""R11: 标签泄漏检测。

造假模式：特征工程过程中使用标签/测试集信息（全局标准化、标签指导的特征选择），
使模型"作弊"获得虚高分数。

输入：分析代码文件或代码目录
输出：CheckResult
"""

import re
from pathlib import Path
from typing import Optional

from .base import BaseChecker, CheckResult, Finding, Status


class R11LabelLeakageChecker(BaseChecker):
    rule_id = "R11"
    rule_name = "标签泄漏检测"
    input_kind = "code"

    # 危险模式：全局数据操作使用了标签信息
    LEAK_PATTERNS = [
        # Python
        (r"(?:StandardScaler|MinMaxScaler|RobustScaler|normalize)\(\)\.fit_transform\(\s*(?:.*?(?:X|data|df))", "全局标准化"),
        (r"SelectKBest\s*\(.*?\)\.fit_transform\(\s*(?:.*?X|.*?data)", "基于标签的特征选择"),
        (r"SelectFromModel\s*\(.*?\)\.fit_transform\(\s*(?:.*?X|.*?data)", "基于模型的全局特征选择"),
        (r"pca|PCA\s*\(\).fit_transform\(\s*(?:.*?X|.*?data)", "全局PCA降维"),
        (r"TfidfVectorizer|CountVectorizer\s*\(\).fit_transform\(\s*(?:.*?X|.*?data)", "全局文本特征化"),
        # R
        (r"scale\s*\(\s*(?:data|df|expr|mat)", "R全局标准化"),
        (r"prcomp\s*\(\s*(?:data|df|expr|mat)", "R全局PCA"),
        (r"nearZeroVar\s*\(\s*(?:data|df|expr|mat)", "R全局零方差过滤"),
    ]

    # 安全模式：分步 fit → transform
    SAFE_PATTERNS = [
        r"\.fit\(\s*(?:X_train|train)",
        r"\.fit_transform\(\s*(?:X_train|train)",
        r"\.transform\(\s*(?:X_test|test|X_val|val)",
    ]

    # proxy variable 关键词（可能与标签高度相关但不是因果关系）
    PROXY_KEYWORDS = [
        "batch_id", "sample_id", "patient_id", "run_id",
        "file_path", "filename", "image_name",
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
                description=f"未找到代码文件：{data_path}",
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

            # 检查 1: 危险的全局数据操作
            safe_count = sum(1 for p in self.SAFE_PATTERNS if re.search(p, content))
            leak_count = 0

            for pattern, desc in self.LEAK_PATTERNS:
                for m in re.finditer(pattern, content, re.IGNORECASE):
                    leak_count += 1
                    line_no = content[:m.start()].count("\n") + 1
                    result.findings.append(Finding(
                        description=f"标签泄漏风险：{desc} ({m.group(0)[:60]})。" +
                                     "若在 train/test split 前执行，导致测试集信息泄漏到训练。",
                        location=f"{fpath.name}:L{line_no}",
                        value=m.group(0)[:80],
                        expected="仅在训练集上 fit，再对测试集 transform",
                        severity="high" if safe_count == 0 else "medium",
                    ))

            # 检查 2: proxy variables（与标签高度相关但非因果）
            for kw in self.PROXY_KEYWORDS:
                # 检查是否作为特征列使用，而不是作为 sample_id
                patterns = [
                    rf"features?\s*\[.*?['\"]{kw}['\"]",
                    rf"X\s*\[.*?['\"]{kw}['\"]",
                    rf"columns.*?{kw}",
                ]
                for pat in patterns:
                    if re.search(pat, content, re.IGNORECASE):
                        # 确认不是样本ID
                        id_patterns = [r"index_col", r"rownames", r"sample_id", r"set_index"]
                        is_id = any(re.search(p, content, re.IGNORECASE) for p in id_patterns)
                        if not is_id:
                            result.status = Status.WARN
                            result.findings.append(Finding(
                                description=(
                                    f"潜在 proxy variable：'{kw}' 被用作特征列。" +
                                    f"可能与标签相关但非因果关系，可能导致过拟合。"
                                ),
                                location=f"{fpath.name}",
                                value=kw,
                                expected="移除或明确标注为 proxy",
                                severity="medium",
                            ))
                            break

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
