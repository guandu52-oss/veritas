# PaperFraud 知识库

> 结构化规则库，驱动静态材料核查。三层架构：造假模式 → 检测规则 → 报告标准。

## 目录结构

```
knowledge/
├── README.md                          # 本文件
├── detection_rules/                   # Layer 2: 检测规则
│   ├── study_design.yaml              # 研究设计审计 (12 条)
│   ├── confounding.yaml               # 混杂控制评估 (8 条)
│   ├── reporting_standards.yaml       # 报告规范符合性 (10 条)
│   ├── statistical_methods.yaml       # 统计方法审查 (8 条)
│   └── numerical_forensics.yaml       # 数值取证规则 (6 条)
├── fraud_patterns/                    # Layer 1: 造假模式
│   └── text_patterns.yaml             # 文本造假模式 (4 条)
└── reporting_standards/               # Layer 3: 报告标准参考
    └── consort.yaml                   # CONSORT 2010 (25 项)
```

## 规则统计

| 类别 | 规则数 | 类型 |
|------|--------|------|
| 研究设计审计 | 12 | methodology_review |
| 混杂控制评估 | 8 | methodology_review |
| 报告规范符合性 | 10 | methodology_review |
| 统计方法审查 | 8 | methodology_review + fraud_detection |
| 数值取证 | 6 | fraud_detection |
| 文本造假模式 | 4 | fraud_detection |
| **合计** | **48** | |

## 规则类型

- **methodology_review**: 方法论合理性审核。检测研究设计是否合理、统计方法是否恰当。设计缺陷 ≠ 学术不端。
- **fraud_detection**: 数据/文本造假检测。检测数值编造、图像篡改、文本模板化等学术不端信号。

## Veritas pipeline 用法

在 Veritas 中不要直接从 `paperfraud.knowledge` 导入。当前一方入口是：

```python
from pathlib import Path

from engine.static_audit.tools.paperfraud_rules import (
    paperfraud_findings_from_matches,
    run_paperfraud_rule_match,
)

artifact = run_paperfraud_rule_match(
    Path("outputs/<case>/research-integrity-audit/full.md"),
    Path("outputs/<case>/research-integrity-audit/paperfraud_rule_matches.json"),
)
findings = paperfraud_findings_from_matches(artifact)
```

`audit-paper` 会在 MinerU 生成 `full.md` 后自动运行该工具：

```text
full.md
  -> paperfraud.rule_match
  -> paperfraud_rule_matches.json
  -> StaticAuditBundle.findings[PF-*]
  -> final_audit_report.html / PaperFraud 规则库命中
```

Tool Registry 条目：

```text
tool_id: paperfraud.rule_match
step_key: paperfraud_rule_match
artifact: paperfraud_rule_matches.json
```

这些规则命中是 reviewer prompts，不是最终学术不端判定。报告中必须保持“需人工复核”的措辞。

## 直接 API 用法

### 1. Python API

```python
from engine.static_audit.adapters.paperfraud_knowledge import (
    generate_reviewer_form,
    load_knowledge_base,
    match_rules,
)

# 加载规则：当前应为 48 条
rules = load_knowledge_base()

# 匹配论文文本
matches = match_rules(rules, paper_full_text=full_text)
for match in matches:
    if match.triggered:
        print(f"[{match.rule.severity}] {match.rule.id}: {match.rule.title}")

form = generate_reviewer_form(rules)
```

### 2. 生成 Reviewer 评分表

```python
from engine.static_audit.adapters.paperfraud_knowledge import (
    generate_reviewer_form,
    load_knowledge_base,
)

form = generate_reviewer_form(load_knowledge_base())
# → 每行一条规则，包含 Y/N/Partial 评分字段
```

### 3. 添加新规则

1. 在 `detection_rules/` 下找到对应类别 YAML 或新建文件
2. 按规则 schema 添加 YAML 条目
3. 重新运行检测 — 新规则自动生效，无需改代码

## 维护指南

- 规则由领域专家维护，通过 PR 方式提交修改
- 每条规则必须有 `human_review` 字段（Reviewer 复核指引）
- 造假检测类规则 (`fraud_detection`) 需附上 `references`（文献依据）
- 方法论审核类规则 (`methodology_review`) 的 severity 不应轻易设为 red
