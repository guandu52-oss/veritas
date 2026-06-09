# AGENTS.md

本文件是后续 AI 编码 Agent 进入本仓库时必须先读的项目操作指南。目标是避免上下文丢失后把项目方向拉偏。

## 项目定位

**Veritas 是一个实验室内部论文风控工具（当前聚焦干实验论文子集），帮助导师（通讯作者）在投稿前主动发现学生数据中的问题，填补监管真空，避免背锅。**

### 核心动机

**问题论文频发，导师由于脱离科研一线，导致监管真空，导师本人并不知情，无法核实数据真伪。**

- 导师给学生挂名通讯作者，但学生可能存在数据造假
- 导师脱离一线，不知道"该查什么"、不知道"是否正常"
- 导师和学生之间存在信息不对称
- 投稿前缺乏有效的自查机制

**Veritas 要解决的是：导师"不知情"的问题。** 工具主动暴露问题模式，打破信息不对称。

### 核心价值（必须强化）

1. **Source Data 内部一致性检测**（最关键）
   - Duplicate columns（不同列名，相同数据）
   - Fixed difference / fixed ratio（固定差值/比例，可能是人为编码或公式派生）
   - Row-offset patterns（行偏移重复，可能是复制粘贴）
   - 跨 sheet 重复（同一实验被包装成多个实验）
   - 数值分布异常（过于完美的正态分布、过少的异常值）

2. **图像操控检测**（高优先级）
   - Exact duplicates（字节级完全重复）
   - Copy-move detection（图内区域复制粘贴，如 Western blot 条带复制）
   - TruFor 伪造检测（神经网络检测图像篡改区域）
   - Panel-level 独立检测（拆分 panel 后对每个子图独立检测）

3. **Claim-to-source-data 映射**（重要）
   - 从 sheet 级推进到 column-block 级
   - 数值复算对比（论文说 mean=2.3±0.4，source data 算出来是 mean=2.1±0.5）
   - Claim 无法被数据支撑的发现

### 问题分层（Issue Categories）

所有 finding 必须分层，帮助导师判断优先级：

| 类别 | 含义 | 示例 | 典型风险级别 |
|---|---|---|---|
| **consistency**（一致性） | 数据内部矛盾，可能造假信号 | 重复列、固定关系、图像 copy-move | high/critical |
| **matching**（匹配性） | 论文与数据不符，claim 无法支撑 | 数值不一致、图表对不上 | medium/high |
| **completeness**（完整性） | 监管真空，学生未提交该有的东西 | 缺 Source Data、缺代码、缺环境文件 | low/medium |

**优先级**：consistency > matching > completeness

**原因**：
- consistency（数据造假）最严重，直接指向学术不端
- matching（claim 不符）次之，可能是笔误或理解偏差
- completeness（材料缺失）最轻，可能只是学生疏忽，但也可能是刻意隐瞒

### 当前能力边界（诚实声明）

- **材料缺失检测**：保留作为 completeness issue，是监管真空的信号（"学生没提交 Source Data → 可能数据不存在、被篡改、或学生在隐瞒"）
- **代码执行审查**：当前未接入 runtime，标记为 `execution_status: not_provided`，作为 completeness issue（"无法验证数据是否可复现"）
- **代码/环境文件**：PI 可以直接让学生补充，但系统仍然标记"未提供"作为完整性问题

### 工程约束

- 报告按 issue_category 分层呈现：高危发现（consistency）→ 匹配问题（matching）→ 完整性问题（completeness）
- 每个 finding 给出明确的"建议行动"（如"立即要求学生解释"、"核对计算过程"、"要求学生提交代码"）
- 报告重点呈现"高危发现 Top 5"和"人工复核任务清单"
- 当前聚焦干实验论文（Python/R 医学生信与生物医药），暂不泛化到湿实验、临床试验等

## 当前范围

MVP 聚焦：

- **干实验论文**：Python/R 医学生信与生物医药干实验论文（不泛化到湿实验）
- 投稿前技术复核，而不是学术价值评价
- 服务式流程：用户提交材料，我们代跑
- CLI-first，同时提供 Web P1 工作台用于内测 happy path
- opencode Agent 编排不确定推断，确定性脚本负责可重复检查

当前明确不做：

- 最终科研诚信判定
- 自动修改论文、Source Data 或代码
- 自动提交 patch
- 完整 SaaS 任务系统和多租户运营后台
- 远程 worker 集群
- 湿实验、临床试验、材料科学等非干实验论文（后续再泛化）

历史决策：先做最简单的一版验证，不急着铺完整 runtime；但 `audit-paper` 入口必须接入 opencode，不能退化成纯确定性脚本。

最小验证目标是：

```text
输入论文
-> opencode agent_plan 生成审查计划和确定性脚本参数
-> Python orchestrator 校验 Tool Registry 中允许的 tool_id
-> research-integrity-auditor / MinerU 做 PDF 解析和静态 evidence ledger
-> 确定性脚本做 numeric/source-data/image checks
-> opencode AgentInvestigationPlanner 基于已生成 artifacts 选择最多 3 轮后续确定性调查工具
-> opencode agent_review 读取结构化产物做 claim/finding 复核
-> opencode role layer 顺序执行 ClaimExtractor / SourceDataAuditor / JudgeAgent
-> 产出结构化证据草案和 Markdown 报告
-> 再决定 runtime / report 的下一步实现
```

补充约束：PDF 解析、evidence ledger、numeric forensics、exact image duplicate 属于论文输入后的固定静态链路；image similarity 属于 Agent-selectable optional investigation tool。Source Data 不再假设一定存在或一定是 CSV/XLSX。当前实现先写 `material_inventory.json`，再由 `agent_material_plan` 或确定性 fallback 选择 optional evidence lane；只有被 Tool Registry 支持且根目录合法的 lane 才能进入执行。

最新补充：`image_similarity_candidates` 已从固定 baseline 移到 Agent-selectable investigation tool。`AgentInvestigationPlanner` 只能选择 Tool Registry 中 `agent_selectable=True` 且 deterministic 的工具；执行记录写入 `investigation_rounds.jsonl`，追加工具输出写入 `workdir/investigation/`，不得覆盖 baseline artifacts。

也就是说，当前第一刀不是直接做完整 `veritas.yml -> runtime -> report`，而是先验证：

> opencode + `third_party/research-integrity-auditor` skill 是否能支撑论文输入后的证据抽取、确定性脚本编排和结构化调查闭环。

用户会自行寻找输入论文。拿到论文后，优先围绕这条最小路线做验证。

## 当前内测增强路线

老板演示 demo 已完成。最新决策：下一阶段面向内测 happy path，允许完整借鉴 ELIS (Scientific Integrity System) 的图像取证栈，优先增强静态审查的视觉证据能力。

目标能力：

```text
PDF / MinerU images
-> canonical figure_evidence
-> ELIS-style pdf-extractor / panel-extractor
-> copy-move dense/keypoint detection
-> TruFor forged-region heatmap
-> CBIR + Milvus single-paper internal similarity
-> AgentInvestigationPlanner 选择后续视觉调查工具
-> HTML visual evidence package
-> human review checklist
```

工程边界：

- ELIS 是能力来源和架构参考，不是 Veritas 主服务。
- 不直接把 ELIS FastAPI/Celery/MongoDB/Redis/Web UI 接进主链路。
- 可以复用 `third_party/elis/system_modules/elis-frontend` 的 Vite/React/Tailwind/布局基础设施，但 Veritas 前端必须放在 `web/frontend/`，业务流程和视觉语言必须是一方实现。
- 先把 ELIS 能力封装成 adapter/tool，注册到 `engine/tools/registry.py`，再由 orchestrator/runtime 执行。
- `figure_evidence` 是 canonical 图像证据入口；panel、mask、heatmap、CBIR match 都必须回链到 canonical figure/panel id。
- 重型视觉工具可以在 happy path 内测中失败隔离；失败必须写入 manifest、`investigation_rounds.jsonl` 和报告 limitations。
- 视觉工具输出只作为候选事实和人工复核任务，不构成最终科研诚信判定。

## 当前 1 周 Demo 方向

完整 demo 的目标仍然是：

```text
veritas.yml / veritas.json
-> PDF 解析
-> Agent claim-to-code mapping
-> precheck
-> subprocess eval run
-> claim match
-> vLLM 图表初筛
-> Markdown/PDF 报告
```

但实现顺序调整为：

1. 先验证论文解析和 evidence ledger
2. 再验证 opencode Agent 能否做 claim-to-code mapping
3. 再补 `veritas.yml`
4. 再补 subprocess runtime 和报告

## 开发前先读

做任何实质改动前，先读：

1. `README.md`
2. `AGENTS.md`
3. `configs/opencode/README.md`
4. `configs/opencode/veritas-agent.md`
5. `configs/opencode/biomed-research-audit-methodology.md`
6. `configs/methodology/`
7. `engine/tools/registry.py`

`docs/` 现在作为产品、开发和决策文档进入仓库。后续 Agent 应优先读取相关 `docs/product/` 和 `docs/development/` 文档，但不要把真实论文、真实运行产物或密钥写入 `docs/`。

如果要修改 opencode 论文审查上下文、skill 或领域先验，先读：

- `configs/opencode/README.md`

## 仓库结构

本仓库是孵化仓，不是成熟 SDK 包。

```text
cli/          CLI demo 入口
engine/       claim 审计、静态审查内核和报告逻辑
runtime/      本地执行后端，未来可能独立成服务
protocols/    垂直领域规则，先从医学生信开始
docs/         产品、开发、决策和本地参考文档
examples/     demo 输入和 manifest
web/          Web P1：stdlib backend + Vite React frontend
third_party/  外部参考仓库和能力吸收区
outputs/      报告和本地运行产物
tests/        单测、集成测试和 e2e 测试
```

`engine/tools/registry.py` 是产品运行时允许执行的确定性工具集合。opencode skill 和 methodology 可以描述工具，但 `audit-paper` 只能执行 registry 允许的 tool_id。

`engine/static_audit/` 是当前 `audit-paper` 的 first-party 静态审查内核。后续新增静态审查 schema、role、tool、orchestrator 行为，优先放在这里，不要继续把产品逻辑堆进 `scripts/`。

当前 role 层不是从 `agent_review` 派生的假 trace。`ClaimExtractor`、`SourceDataAuditor`、`JudgeAgent` 已通过 `engine.investigation.opencode_agent.run_agent_role()` 独立调用 opencode；成功 role 在未指定 `--force` 时会复用已有 output/trace，避免重复调用把成功结果覆盖成失败结果。

不要把 `runtime/` 移到 `engine/` 下面。`runtime/` 是一级产品原语。

## 当前产品范围

MVP 聚焦：

- Python/R 医学生信论文
- 服务式流程：用户提交，我们代跑
- PI / 课题组是第一付费方和主要报告读者
- CLI-first 内部 demo

MVP 明确不做：

- 完整 SaaS 任务提交
- 远程 worker 集群
- 长训练
- 自动改代码
- 自动提交 patch
- 学术价值判断
- 最终诚信判定

## 当前开发优先级

最新优先级：

1. 定义 canonical `figure_evidence` / `panel_evidence` / `visual_finding` / `image_relationship` schema。
2. 以 adapter 方式接入 ELIS-style 图像取证工具：pdf-extractor、panel-extractor、copy-move、TruFor、CBIR/Milvus。
3. 将 ELIS-style 工具注册进 Tool Registry，并接入 AgentInvestigationPlanner。
4. HTML 报告增加视觉证据包和人工复核 checklist。
5. 把 investigation 追加产物并入 canonical finding/evidence 图。
6. 验证 opencode SDK / opencode 风格 Agent 能否接入 claim-to-code mapping。
7. 定义 `veritas.yml` schema，YAML 主、JSON 兼容。
8. 增强 subprocess runtime，产出结构化 execution evidence。
9. 接百炼 Qwen vLLM 做图表初筛。
10. 生成 Markdown/PDF 报告，支持作者视图和 PI 视图。

加入真实外部集成时，如果短期阻塞 demo，先做 typed adapter + mock fixture，并在文档中写清缺口。

## 核心设计规则

### Evidence First

报告必须从结构化 evidence event 生成，不能直接从 Agent 自然语言总结生成。

至少支持：

- `file_evidence`
- `execution_evidence`
- `claim_match`
- `figure_evidence`

### Agent 边界

Agent 可以：

- 把 claim 映射到代码和产物
- 识别入口脚本和结果文件候选
- 生成结构化 JSON trace
- 在 `agent_plan` 中选择 Tool Registry 允许的 tool_id 并填写参数
- 写入 `outputs/`

Agent 不可以：

- 自动编辑源码
- 自动应用 patch
- 自动提交 commit
- 判定最终学术价值或学术不端
- 写入 `outputs/` 之外的目录
- 绕过 Tool Registry 直接执行任意工具或命令

Agent 输出必须结构化。不符合 schema 时，用 Pydantic 校验错误反馈给 Agent 重试。

当前实现用轻量 Python validator 做 schema 校验；如果后续引入 Pydantic，保持“校验失败 -> 把错误反馈给 Agent 重试 -> 仍失败则 warning/failed trace，不覆盖确定性证据”的语义。

### Runtime 边界

Runtime 负责执行命令和记录证据。Runtime 不是 Agent。

MVP 最终需要支持 subprocess 执行。Docker 先保留接口。

执行层需要记录：

- command manifest
- stdout/stderr
- exit code
- runtime seconds
- result files
- file hashes

### PDF 解析

PDF 解析优先参考：

- `third_party/research-integrity-auditor`

它使用 MinerU 做 PDF 转换和 evidence ledger 构建。

不要把 token 写入文件、报告或日志。`MINERU_API_TOKEN` 从环境变量读取。

### 图表初筛

图表视觉初筛计划使用百炼 Qwen vLLM。

vLLM 输出只是初筛信号，不是最终证据。高风险项必须进入人工复核字段。

## CLI 合约

目标命令：

```bash
veritas init <project_dir>
veritas precheck <veritas.yml>
veritas run <veritas.yml>
veritas report <report.json>
```

当前可运行开发命令：

```bash
PYTHONPATH=. python3 cli/main.py audit-paper <paper_dir> --case-id <case_id> --agent-mode review --progress plain
PYTHONPATH=. python3 cli/main.py precheck examples/bioinfo_python_case/veritas.json
PYTHONPATH=. python3 cli/main.py run examples/bioinfo_python_case/veritas.json --output-dir outputs/demo
PYTHONPATH=. python3 cli/main.py report outputs/demo/report.json --output-dir outputs/demo
```

当前老板 demo 推荐使用：

```bash
PYTHONPATH=. python3 cli/main.py audit-paper <paper_dir> --case-id <case_id> --agent-mode review --agent-timeout-seconds 180 --agent-max-retries 1 --progress plain
```

优先展示 `outputs/<case_id>/research-integrity-audit/final_audit_report.html`。该 HTML 是单文件静态报告，围绕 Top-N priority findings、证据定位、良性解释、人工复核动作和 role trace 展示；不要把它表述成最终科研诚信判定。`audit-paper` 进度写入 `stderr`，最终 summary JSON 写入 `stdout`，不要把进度事件混入最终 JSON；MinerU 子进程输出可以作为 `command_output` 进度事件转发。

`veritas run` 默认按 `eval` 深度设计。

## 测试要求

核心行为要测试驱动。

MVP 最低测试范围：

- schema test
- CLI smoke test
- claim matcher test
- runtime subprocess test
- report render test
- Agent structured-output validation test

涉及外部服务的集成，先加 fixture-based test。

## 第三方仓库使用原则

`third_party/` 是能力吸收区，不是主产品源码。

使用方式：

- `research-integrity-auditor`：借 MinerU 流程、evidence ledger、谨慎风险语言
- `elis`：借图像取证工具栈、panel/copy-move/TruFor/CBIR 思路和视觉证据包，不直接复用其 SaaS 主服务
- `deepwiki-open`：借 repo 理解和 Mermaid 图思路
- `AsyncReview`：借递归调查和工具调用模式

不要把大型第三方内部实现直接 import 进主链路。先用本地 adapter 包起来。

## 文档驱动开发

本项目采用文档驱动开发，`docs/` 是需要维护的项目文档区。

如果改动影响产品行为，请优先同步更新：

- `README.md`
- `AGENTS.md`
- `configs/opencode/`

如果改动影响已落地的 Web/Agent/runtime 计划，也要同步更新 `docs/product/` 或 `docs/development/` 中对应文档。

如果新决策改变了旧决策，新增一份 decision record，不要静默覆盖历史。

## 工程注意事项

- 后端、Agent、runtime、reporting 优先使用 Python
- Web 已被用户明确要求进入 P1；前端基础设施复用 ELIS 的 Vite/React/Tailwind 模式，后端仍保持 Python 主导
- 生成报告和运行产物放在 `outputs/`
- 不要把 secrets 写进 manifest、报告、日志或文档
- 不要提交 `__pycache__` 等缓存产物
