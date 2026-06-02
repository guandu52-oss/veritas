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

## 如何使用

### 1. Python API

```python
from paperfraud.knowledge import KnowledgeBase, RuleMatcher

# 加载规则
kb = KnowledgeBase()
kb.load_all()
print(kb)  # KnowledgeBase(5 rule sets, 44 rules)

# 查询
red_rules = kb.by_severity("red")
study_rules = kb.get_category("study_design")

# 搜索
results = kb.search("样本量")

# 匹配论文文本
matcher = RuleMatcher()
matches = matcher.match_rules(study_rules.rules, paper_text=full_text)
for m in matches:
    if m.triggered:
        print(f"  [{m.rule.severity}] {m.rule.title}")
        print(f"  Evidence: {m.format_evidence()}")
```

### 2. 生成 Reviewer 评分表

```python
kb = KnowledgeBase()
kb.load_all()
form = kb.reviewer_form(["study_design", "confounding", "reporting_standards"])
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
