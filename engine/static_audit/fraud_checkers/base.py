"""检测器基类 + 统一输入/输出格式。

所有 checker 继承 BaseChecker，实现 check() 方法。
输入：data_path（文件路径或目录）+ metadata（可选字典）
输出：CheckResult namedtuple
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from pathlib import Path
from enum import Enum


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class Finding:
    """单条发现"""
    description: str          # 人类可读描述
    location: Optional[str]   # 定位信息：列名/行号/样本名
    value: Optional[Any]      # 异常值
    expected: Optional[Any]   # 期望值
    severity: str = "medium"  # low / medium / high


@dataclass
class CheckResult:
    """检测器输出"""
    rule_id: str              # R1, R2, ...
    rule_name: str            # 规则中文名
    status: Status            # pass / fail / warn
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""         # 一句话总结
    metadata: dict = field(default_factory=dict)  # 检测过程元数据（耗时、样本量等）

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class BaseChecker:
    """检测器基类"""

    rule_id: str = ""
    rule_name: str = ""
    input_kind: str = "matrix"   # matrix / expression / metadata / code

    def check(self, data_path: str, metadata: Optional[dict] = None) -> CheckResult:
        """子类实现：读入数据 → 检测 → 返回 CheckResult"""
        raise NotImplementedError

    def _load_data(self, data_path: str):
        """通用数据加载。子类可重写。"""
        raise NotImplementedError

    def _summarize(self, result: CheckResult) -> CheckResult:
        """自动填充 summary"""
        n = len(result.findings)
        if n == 0:
            result.summary = f"[{self.rule_id}] {self.rule_name}：未发现异常"
        else:
            highs = [f for f in result.findings if f.severity == "high"]
            result.summary = f"[{self.rule_id}] {self.rule_name}：发现 {n} 条异常" + \
                             (f"（含 {len(highs)} 条高危）" if highs else "")
        return result
