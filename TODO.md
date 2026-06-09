# Veritas 当前 TODO

更新时间：2026-05-29

## 产品定位（最新）

**Veritas 是一个实验室内部论文风控工具（当前聚焦干实验论文子集），帮助导师（通讯作者）在投稿前主动发现学生数据中的问题，填补监管真空，避免背锅。**

**核心动机**：问题论文频发，导师由于脱离科研一线，导致监管真空，导师本人并不知情，无法核实数据真伪。

**核心价值**：
- Source Data 内部一致性检测（重复列、固定关系、数值异常）
- 图像操控检测（copy-move、伪造区域、跨图重复）
- Claim-to-source-data 映射（论文与数据不符的发现）

**问题分层（Issue Categories）**：所有 finding 按 `consistency`（一致性，最严重）> `matching`（匹配性）> `completeness`（完整性，材料缺失）分层，帮助导师判断优先级。

- 材料缺失检测保留作为 completeness issue（监管真空的信号）
- 代码执行审查未接入保留作为 completeness issue（可验证性缺失）

**当前范围**：干实验论文（Python/R 医学生信与生物医药），暂不泛化到湿实验、临床试验等。

## 当前目标

把 `audit-paper` 从单 case 验证推进到可泛化的静态审查 demo，并把 Agent 从后置 reviewer 升级为受控的 investigation planner。

最新产品阶段已经从"老板演示 demo"推进到"内测 happy path"。因此下一阶段允许借鉴并接入 ELIS (Scientific Integrity System) 的完整图像取证思路，包括 PDF 图片提取、panel 拆分、copy-move、TruFor、CBIR/Milvus 和可视化证据包；但这些能力必须先通过 Veritas adapter、Tool Registry 和 runtime 接口进入，不得直接把 ELIS FastAPI/Celery/MongoDB/Redis/Web UI 主服务变成 Veritas 主链路。

当前原则：

- 真实论文 case 只能作为 fixture，不得成为默认逻辑。
- PDF 静态链路默认尝试执行。
- Source Data、raw data、代码仓库、环境文件都是 optional evidence lane，由材料发现和 Agent 计划决定。
- Agent 可以决定"查什么、为什么查、用什么参数"，但只能选择 Tool Registry 允许的确定性工具。
- 报告展示 Top-N priority findings，不假设某个 case 固定只有 3 个发现。
- 不在运行代码、常驻方法论或默认配置里写入具体论文标题、文件名前缀、图号、finding id 或 claim 文本。
- ELIS-style 重型图像取证能力可以进入内测 happy path，但必须保留失败隔离、超时、artifact provenance 和人工复核入口。
- **报告按 issue_category 分层呈现**：高危发现（consistency）→ 匹配问题（matching）→ 完整性问题（completeness）。

## 已完成

- `audit-paper` 接入 `material_inventory.json`，Source Data 执行从固定目录发现改为 registry-backed optional lane。
- `agent_material_plan` 根据材料清单选择 optional evidence lane；失败时 deterministic fallback。
- `scripts/run_paper_audit.py` 已折叠为 9 行兼容 wrapper；编排逻辑移入 `engine/static_audit/orchestrator.py` 的 `run_static_audit()`。
- CLI 新增 `--progress auto|plain|jsonl|off` 标志，进度写入 stderr，最终 summary JSON 写入 stdout；MinerU 子进程输出转发为 `OUT mineru` 进度行。
- `--agent-mode review` 成为老板 demo 推荐默认值；`full` 额外执行 `agent_plan` 但可能遇非 JSON 输出。
- `engine/tools/registry.py` 扩展：新增 `execution_phase`（mandatory_bootstrap / agent_selectable / reserved / report_only）、`agent_selectable`、`input_artifacts`、`output_artifacts`、`param_schema` 元数据；新增 `tool_catalog_for_investigation()`、`validate_investigation_tool_action()`、`coerce_tool_params()`。
- 新增工具注册：`material.inventory`、`agent.material_plan`、`source_data.pair_forensics`、`image.similarity_candidates`、`static_audit.bundle`、`agent.role.claim_extractor`、`agent.role.source_data_auditor`、`agent.role.judge`、`report.render_static_html`。
- `image_similarity_candidates` 已从固定 baseline 移为 Agent-selectable optional investigation tool，输出写入 `workdir/investigation/`，不覆盖 baseline artifacts。
- Agent role 层：`ClaimExtractor`、`SourceDataAuditor`、`JudgeAgent` 通过 `engine.investigation.opencode_agent.run_agent_role()` 独立调用 opencode；成功 trace 在未指定 `--force` 时复用，避免覆盖。
- `engine/investigation/opencode_agent.py` 新增 `fake_material_plan`、`fake_investigation_plan`、`fake_role_output`、`validate_material_plan`、`validate_investigation_plan`、`validate_role_output`。
- `AgentInvestigationPlanner` P0 最小闭环：最多 3 轮规划、Tool Registry 校验、独立 investigation artifacts、`investigation_rounds.jsonl` 和 HTML 摘要展示。
- HTML 报告：`final_audit_report.html` 单文件静态报告，围绕 Top-N priority findings、证据定位、良性解释、人工复核动作和 role trace 展示。
- HTML 模板层 UI 中文化，保留专业术语和溯源证据原文。
- Agent prompt 增加中文自然语言输出约束。
- README、AGENTS、CLI 文档的默认命令改为 `<paper_dir>` / `<case_id>` 模板。
- `source_data_pair_forensics` 已作为通用 pair/row-offset Source Data 取证工具进入静态审查链路。
- MinerU 远端接口断连时，orchestrator 已增加产品层 3 次尝试、退避等待和 `stdout_tail` 失败摘要。
- 方法论拆分：`biomed-research-audit-methodology.md` 折叠为入口索引，领域规则拆到 `configs/methodology/`（general.md、source-data.md、biomed-wetlab.md、bioinfo.md、visual-forensics.md）。
- `opencode.json` context 已包含 `configs/methodology/` 下所有方法论文件。
- `pyproject.toml` 已包含 `web*` 包。
- Web P1 基础设施：`web/backend/` stdlib backend + `web/frontend/` Vite React frontend。

## 下一步优先级

### P0: ELIS-style 图像取证栈内测闭环

状态：已确认采用完整 ELIS-style 路线作为内测增强方向。当前目标不是稳定泛化所有论文，而是在 happy path 下让内测用户看到明显强于"opencode + skill 静态审查"的图像取证能力。

目标：形成"canonical figure evidence -> ELIS adapter tools -> AgentInvestigationPlanner 选择/解释 -> HTML 视觉证据包 -> 人工复核任务"的受控闭环。

已确认的产品/工程决策：

- 全量借鉴 ELIS 能力方向：`pdf-extractor`、`panel-extractor`、`copy-move-detection`、`copy-move-detection-keypoint`、`TruFor`、`CBIR + Milvus` 都进入内测路线。
- 不直接复用 ELIS 的 FastAPI/Celery/MongoDB/Redis 主服务；Veritas 只吸收工具能力和数据模型思想。
- 每个 ELIS 能力先封装为 adapter/tool，注册到 `engine/tools/registry.py`，再由 orchestrator/runtime 执行。
- 先文件驱动：tool input/output、job trace、evidence event、visual evidence package 都写入 `outputs/<case_id>/`。
- `figure_evidence` 必须是 canonical 图像证据模型；MinerU 图片、ELIS pdf-extractor 图片、panel crop、copy-move mask、TruFor heatmap、CBIR match 都必须回链到 canonical figure/panel id。
- ELIS 工具失败不应阻断整篇审查；失败进入 `investigation_rounds.jsonl`、run manifest 和报告 limitations。
- 低置信视觉发现不得直接写成不端结论；报告只能表达"可疑视觉模式 + 证据位置 + 良性解释 + 人工复核动作"。

建议实施顺序：

1. 定义 `figure_evidence` / `panel_evidence` / `visual_finding` / `image_relationship` schema。
2. 建立 `engine/static_audit/tools/elis_adapters/`，先做 adapter contract 和 mock/fixture 输出。
3. 接入 ELIS `pdf-extractor` 和 `panel-extractor`，解决 canonical image id、page、figure caption、panel crop 的统一。
4. 接入 copy-move 两类工具，输出 mask/overlay、method、score、target panel、candidate region。
5. 接入 TruFor，输出 heatmap/score/threshold/model metadata，默认只作为候选信号。
6. 接入 CBIR + Milvus 内测路径，先做单 case/单论文内部索引，保留未来跨论文 corpus 扩展点。
7. HTML 报告新增视觉证据包：原图、panel、overlay/heatmap、候选对、caption/condition、人工复核 checklist。
8. AgentInvestigationPlanner prompt 加入 ELIS tool catalog，使 Agent 能在已有 findings 基础上选择 copy-move、TruFor 或 CBIR。

验收标准：

- 对一个 happy path 内测 case，`audit-paper` 可以生成含 ELIS-style 视觉证据包的 HTML 报告。
- 每个视觉 finding 都能追溯到输入 PDF、extracted image/panel、tool output、score 和人工复核动作。
- 任一 ELIS 工具失败时，主报告仍能生成，并明确标记该视觉检查未完成。
- 不把 ELIS 工具输出直接等同于最终科研诚信判定。

### P0 后续待补：Static Audit 核心打磨

P0 最小闭环已完成。以下 4 项仍需补齐：

1. **Investigation findings 合并策略**：将 investigation 追加产物中的高价值 findings 合并进 canonical evidence/finding 表时，需要明确去重和优先级策略。建议基于 `tool_id + finding category + workbook/sheet/rows/columns/image pair + support` 去重。
2. **Pydantic schema 升级**：把当前轻量 validator 升级为 Pydantic schema，保持"校验失败 -> 反馈 Agent 重试 -> 仍失败则 fallback"的语义。
3. **Planner prompt 优化**：进一步优化 AgentInvestigationPlanner prompt，让其更稳定地区分"补充调查"与"重复 baseline"。
4. **Planner fixture-based eval**：为 `AgentInvestigationPlanner` 增加真实 fixture-based eval，验证它能在已有 artifacts 上选择合理工具，并拒绝重复、缺依赖、越权 Agent tool。

### P0 产品层改进：问题分层与报告呈现

**背景**：根据 leader 反馈，Veritas 的核心场景是"实验室内部风控工具，帮助导师在投稿前主动发现学生数据中的问题"。当前报告把所有 finding 混在一起，导师难以判断优先级。

**目标**：将所有 finding 按 `issue_category` 分层，帮助导师快速定位最严重的问题。

**分层定义**：

| 类别 | 含义 | 示例 | 典型风险级别 |
|---|---|---|---|
| **consistency**（一致性） | 数据内部矛盾，可能造假信号 | 重复列、固定关系、图像 copy-move | high/critical |
| **matching**（匹配性） | 论文与数据不符，claim 无法支撑 | 数值不一致、图表对不上 | medium/high |
| **completeness**（完整性） | 监管真空，学生未提交该有的东西 | 缺 Source Data、缺代码、缺环境文件 | low/medium |

**优先级**：consistency > matching > completeness

**实施计划**：

1. **数据模型扩展**：在 `engine/static_audit/models.py` 的 `Finding` dataclass 中新增 `issue_category: Literal["consistency", "matching", "completeness"]` 字段。
2. **工具输出适配**：
   - `source_data_findings.py` 和 `source_data_pair_forensics.py` 的输出自动标记为 `consistency`
   - `agent_review` 和 role layer 的输出由 Agent 标记 category
   - 材料缺失检测（`missing_materials`）和 execution status 标记为 `completeness`
3. **报告呈现重构**：`engine/static_audit/html_report.py` 按 category 分层呈现：
   - 🚨 高危发现（Consistency Issues）
   - ⚠️ 匹配问题（Matching Issues）
   - ℹ️ 完整性问题（Completeness Issues）
4. **建议行动**：每个 finding 给出明确的"建议行动"（如"立即要求学生解释"、"核对计算过程"、"要求学生提交代码"）。

**验收标准**：

- 对一个真实 case，HTML 报告能按 consistency / matching / completeness 分层展示 findings。
- 每个 finding 都有明确的 `issue_category` 和"建议行动"。
- 材料缺失和 execution status 作为 completeness issue 呈现，而不是放在"方法论"或"当前限制"章节。

### P1: 扩展静态审查 Tool Registry 和泛化验收

1. 增加非 happy-path fixtures。

- 只有 PDF，没有 Source Data。
- PDF + CSV/TSV Source Data，但当前 lane 暂不执行。
- PDF + 多个候选数据目录。
- PDF + 无法解析或损坏的材料文件。
- synthetic fixtures 必须覆盖 wide-format、long-format、CSV/TSV、no-source-data、benign repeated pattern，防止对 paper2 过拟合。

2. 固化材料发现测试。

- 验证 `material_inventory.json` 对 XLSX、CSV/TSV、raw data、archive、image、supplement PDF 的分类。
- 验证 unsupported materials 不会误进入已执行证据。
- 验证 selected lane root 必须位于 `paper_dir` 内。

3. 报告空态和多态测试。

- 无 priority findings 时报告应清楚显示"未生成高优先级 finding"。
- priority findings 超过 Top-N 时报告应显示 `展示 N / total 条`。
- Agent 缺失或失败时，HTML 和 Markdown 都应显示 warning/limitation，而不是空白或误报通过。

4. Agent 结构化输出稳定性。

- 对 `agent_material_plan`、`agent_review`、三个 role agent 增加 fixture-based schema tests。
- 对中文自然语言输出做轻量校验，避免下一轮报告再次大量英文 UI 文案。
- 保留专业术语、文件名、evidence refs、quoted claim 原文，不做强制翻译。

5. 扩展 optional lane 和 Source Data facade。

- 设计统一 `source_data_table` facade，内部按 XLSX/CSV/TSV 分派。
- CSV/TSV 第一版支持 profile、duplicate、fixed-ratio、row-offset 基础检测。
- CSV/TSV 要进入 claim-to-source-data mapping。
- raw data 先只进入材料完整性和人工复核任务，不急着执行。
- 不支持的材料必须进入 `unsupported_materials`。
- `material.completeness_check` 需要核查 data/code/source availability statement 与实际提交材料是否一致，输出 `missing_required_material`、`incomplete_supporting_material`、`unsupported_material`，但不做诚信判断。
- `figure_sheet_mapper` 先用确定性规则匹配 figure id、sheet name、caption，再让 Agent 解释低置信映射；低置信结果进入报告并标记人工复核。

6. ELIS-style 图像取证泛化。

- 在 happy path 可跑通后，再补 no-image、panel split 失败、TruFor 依赖缺失、Milvus 不可用、copy-move 无结果等 fixture。
- 对每类视觉工具建立 synthetic fixture，避免只对某篇论文或某类 PubPeer 评论过拟合。
- 明确 score calibration：工具分数只决定人工复核优先级，不决定最终结论。

### P2: 回到动态执行审查。

- 保留当前 static audit 产物格式。
- 设计 runtime evidence 与 static audit bundle 的合并方式。
- 先接 subprocess runtime，不先做云端 runner。

## 当前验收命令模板

```bash
PYTHONPATH=. python3 cli/main.py audit-paper <paper_dir> \
  --case-id <case_id> \
  --fresh \
  --force \
  --agent-mode review \
  --agent-timeout-seconds 300 \
  --agent-max-retries 1 \
  --progress plain
```

## 红线

- 不把某个 fixture 的论文标题、文件名前缀、图号或 finding id 写入产品逻辑。
- 不把缺失材料解释为审查通过。
- 不让 Agent 绕过 Tool Registry 执行任意命令。
- 不做最终科研诚信判定。
- 不自动改论文、Source Data 或代码。
