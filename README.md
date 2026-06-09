# Veritas

**Veritas 是一个实验室内部论文风控工具（当前聚焦干实验论文子集），帮助导师（通讯作者）在投稿前主动发现学生数据中的问题，填补监管真空，避免背锅。**

**核心动机**：问题论文频发，导师由于脱离科研一线，导致监管真空，导师本人并不知情，无法核实数据真伪。

**核心价值**：
- Source Data 内部一致性检测（重复列、固定关系、数值异常）
- 图像操控检测（copy-move、伪造区域、跨图重复）
- Claim-to-source-data 映射（论文与数据不符的发现）

**问题分层**：所有 finding 按 `consistency`（一致性，最严重）> `matching`（匹配性）> `completeness`（完整性，材料缺失）分层，帮助导师判断优先级。

当前仓库仍以 `audit-paper` 审查闭环为核心，但已开始补 Web P1：在浏览器里创建 case、上传输入、启动与 CLI 等价的审查、观察进度并打开最终 HTML 报告。

## 当前范围

MVP 聚焦：

- **干实验论文**：Python/R 医学生信与生物医药干实验论文（不泛化到湿实验、临床试验等）
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

## 当前内测增强方向

老板演示 demo 已完成。下一阶段目标是让内测用户在 happy path 下体验更强的静态审查能力，尤其是图像和视觉取证。

Veritas 将借鉴 ELIS (Scientific Integrity System) 的完整图像取证思路：

- PDF 图片提取和 panel 拆分。
- copy-move dense/keypoint 图内复用检测。
- TruFor 神经网络伪造检测。
- CBIR + Milvus 单论文内部相似检索。
- 视觉证据包和人工复核 checklist。

边界是：ELIS 能力必须通过 Veritas adapter、Tool Registry 和 runtime 接口接入；不直接复用 ELIS 的 FastAPI/Celery/MongoDB/Redis 主服务。前端可以复用 `third_party/elis/system_modules/elis-frontend` 的 Vite/React/Tailwind 基础设施模式，但产品信息架构、视觉语言和审查流程必须是 Veritas first-party。所有视觉工具输出都只是候选证据和人工复核入口，不做最终科研诚信判定。

## 仓库结构

```text
cli/          CLI demo 入口
engine/       claim 审计、静态审查内核、Agent 调查、报告逻辑
runtime/      本地执行后端，未来可独立成服务
protocols/    垂直领域规则，先从医学生信开始
configs/      opencode 与运行配置
examples/     demo manifest 和轻量样例
scripts/      可复用本地工具脚本
web/          Web P1：stdlib backend + Vite React frontend
tests/        单测、集成测试和 e2e 测试
third_party/  外部参考仓库，以 git submodule 管理
```

`engine/tools/registry.py` 是当前静态审查工具集合的 source of truth。opencode 可以在 `agent_plan` 中选择 tool_id 和填写参数，但只有 Tool Registry 允许的 tool_id 会被 Python orchestrator 执行。

`engine/static_audit/` 是 Veritas first-party 静态审查内核，负责 schema、protocol、roles、tools、orchestrator 和 `static_audit_bundle.json`。`third_party/research-integrity-auditor` 仍作为 upstream reference，已吸收到 `engine/static_audit/upstream/research_integrity_auditor/` 的只读镜像中。

各模块职责和调用关系详见 [CodeMAP.md](CodeMAP.md)。

不进入提交：

- `input/`：真实论文与用户输入材料。
- `outputs/`：本地运行产物与报告。
- `web_data/`：Web 本地 case store 与运行状态。
- `web/frontend/dist/`：前端本地构建产物。
- `web/frontend/node_modules/`：前端依赖。
- `.env`：本地密钥。

## audit-paper 数据流 / Data Flow

The following diagram traces the full `audit-paper` pipeline from raw paper input to final report generation. Each box is a pipeline stage; arrows show data dependencies and artifact outputs written to the workdir.

```text
paper_dir
  |
  +-- discover_pdf()
  |     |
  |     v
  |   paper_pdf
  |
  +-- build_material_inventory()
        |
        v
      material_inventory.json

material_inventory.json + workdir + env
  |
  v
+-----------------------------+
| agent_material_plan         |
| agent_mode != off           |
| opencode -> optional lanes  |
+-----------------------------+
  |
  +-- writes agent_material_plan.json
  +-- selects source_data_xlsx if executable
  +-- records missing/unsupported materials
  |
  v
selected optional lanes + paper_pdf + workdir + env
  |
  v
+-----------------------------+
| agent_plan                  |
| when agent_mode=plan/full   |
| opencode -> tool_id JSON    |
+-----------------------------+
  |
  +-- writes agent_audit_plan.json
  +-- validates tool_id via Tool Registry
  +-- provides source_data_findings params
  |
  v
+-----------------------------+
| MinerU PDF parse            |
| third_party tool            |
+-----------------------------+
  |
  +-- full.md
  +-- images/
  +-- mineru_manifest.json
  |
  v
+-----------------------------+
| deterministic evidence      |
+-----------------------------+
  |
  +-- evidence_ledger.json
  +-- numeric_forensics.json
  +-- source_data_profile.json      (only if selected optional lane is executable)
  +-- source_data_findings.json     (only if selected optional lane is executable)
  +-- source_data_pair_forensics.json (only if selected optional lane is executable)
  +-- exact_image_duplicates.json
  +-- vlm_triage_selected.json  (currently reused or skipped)
  |
  v
+-----------------------------+
| ELIS-style visual forensics |
| next internal beta path     |
+-----------------------------+
  |
  +-- canonical figure_evidence.json
  +-- panel_evidence.json
  +-- copy_move_findings.json
  +-- trufor_findings.json
  +-- image_relationships.json
  +-- visual evidence package
  |
  v
+-----------------------------+
| AgentInvestigationPlanner   |
| agent_mode != off           |
| opencode -> tool actions    |
+-----------------------------+
  |
  +-- validates deterministic tool_id via Tool Registry
  +-- writes agent_investigation_plan_round_XX.json
  +-- writes investigation_rounds.jsonl
  +-- writes investigation/round_XX/action_YY artifacts
      e.g. image_similarity_candidates.json
  |
  v
+-----------------------------+
| agent_review                |
| when agent_mode=review/full |
| opencode -> JSON schema     |
+-----------------------------+
  |
  +-- writes agent_review.json
  +-- candidate claims
  +-- finding reviews
  +-- manual review tasks
  |
  v
+-----------------------------+
| static audit role layer     |
| when agent_mode=review/full |
+-----------------------------+
  |
  +-- ClaimExtractor -> agent_claim_extractor.json
  +-- SourceDataAuditor -> agent_source_data_auditor.json
  +-- JudgeAgent -> agent_judge.json
  +-- reserved roles -> skipped trace JSON
  +-- writes agent_traces/*.json
  |
  v
+-----------------------------+
| generate_report             |
+-----------------------------+
  |
  +-- final_audit_report.md
  +-- final_audit_report.html
  +-- audit_run_manifest.json
  +-- static_audit_bundle.json
  +-- agent_traces/
```

## audit-paper 状态机 / State Machine

The state machine below governs the step-by-step execution order of `audit-paper`. Each node represents a pipeline stage; transitions depend on agent mode, artifact availability, and command exit codes.

```text
START
  |
  v
PARSE_ARGS
  |
  v
DISCOVER_INPUTS
  |
  +-- no PDF ---------------------------> FAILED_EXCEPTION
  |
  v
CREATE_WORKDIR
  |
  +-- fresh=true -> SAFE_REMOVE_WORKDIR
  |
  v
MATERIAL_INVENTORY
  |
  +-- scans paper_dir excluding paper PDF
  +-- writes material_inventory.json
  +-- classifies xlsx/csv/raw/image/archive/supplement materials
  |
  v
AGENT_MATERIAL_PLAN?
  |
  +-- agent_mode != off
  |      |
  |      +-- opencode ok -----------> status=ran
  |      +-- opencode/schema fail --> status=warning + deterministic fallback
  |
  +-- agent_mode off --------------> deterministic fallback
  |
  v
AGENT_PLAN?
  |
  +-- agent_mode in plan/full
  |      |
  |      +-- opencode ok -----------> status=ran
  |      +-- opencode/schema fail --> status=warning
  |
  +-- agent_mode off/review -------> skip plan
  |
  v
MINERU
  |
  +-- outputs exist and force=false -> status=reused
  +-- token missing and no outputs -> status=skipped
  +-- command ok ------------------> status=ran
  +-- command/output fail ---------> status=failed
  |
  v
PDF_DERIVED_STEPS
  |
  +-- full.md exists -> evidence_ledger + numeric_forensics
  +-- full.md missing -> both skipped
  |
  v
SOURCE_DATA_STEPS
  |
  +-- selected source_data_xlsx root valid -> profile -> findings
  +-- no selected executable lane -> skipped
  +-- selected root invalid/outside paper_dir -> skipped
  +-- command/output fail -> status=failed
  |
  v
IMAGE_DUPLICATE_CHECK
  |
  +-- images dir exists -> exact duplicate run/reuse/fail
  +-- images dir missing -> skipped
  +-- image similarity is optional and may be selected by AgentInvestigationPlanner
  |
  v
ELIS_VISUAL_FORENSICS?              (planned internal beta)
  |
  +-- adapter/runtime available -> panel/copy-move/TruFor/CBIR tools
  +-- partial tool failure ------> warning + limitations + continue
  +-- unavailable --------------> skipped
  |
  v
AGENT_INVESTIGATION?
  |
  +-- agent_mode != off
  |      |
  |      +-- up to 3 rounds of opencode investigation planning
  |      +-- planner selects only deterministic agent_selectable Tool Registry entries
  |      +-- invalid/duplicate/missing-dependency actions -> recorded as rejected/skipped
  |      +-- accepted actions -> orchestrator executes and writes investigation_rounds.jsonl
  |
  +-- agent_mode off --------------> skipped
  |
  v
VLM_TRIAGE
  |
  +-- existing artifact -> reused
  +-- otherwise -> skipped
  |
  v
AGENT_REVIEW?
  |
  +-- agent_mode in review/full
  |      |
  |      +-- opencode ok -----------> status=ran
  |      +-- opencode/schema fail --> status=warning
  |
  +-- agent_mode off/plan ---------> skip review
  |
  v
AGENT_ROLES?
  |
  +-- existing successful role trace and force=false -> status=reused
  +-- agent_mode in review/full -> ClaimExtractor -> SourceDataAuditor -> JudgeAgent
  +-- opencode ok -----------> trace status=ran
  +-- opencode/schema fail --> step status=warning, trace status=failed
  +-- reserved roles --------> trace status=skipped
  |
  v
GENERATE_REPORT
  |
  v
WRITE_MANIFEST
  |
  +-- any status=failed -> EXIT 1
  +-- no failed steps  -> EXIT 0
```

状态含义 / Step status values：

- `ran`：本轮真实执行成功。/ Genuinely executed and succeeded in this run.
- `reused`：目标产物已存在且未指定 `--force`。/ Target artifact already exists and `--force` was not specified.
- `skipped`：前置材料或能力缺失，跳过但不视为失败。/ Prerequisite material or capability missing — skipped but not treated as a failure.
- `warning`：Agent 失败或输出不合规，降级继续确定性报告。/ Agent failed or produced non-compliant output — degraded gracefully, deterministic reporting continues.
- `failed`：确定性命令失败或预期产物缺失，最终进程返回 1。/ Deterministic command failed or expected artifact is missing — final process exits with code 1.

当前 `audit-paper` 的真实 Agent role 层顺序执行 3 个角色：`ClaimExtractor`、`SourceDataAuditor`、`JudgeAgent`。其余 role 先写入 `skipped` trace，占位给后续并行 subagent 和视觉/数字/数学/领域复核扩展。

The Agent role layer in `audit-paper` currently executes three roles in sequence: `ClaimExtractor`, `SourceDataAuditor`, and `JudgeAgent`. Remaining roles are written as `skipped` traces for now, reserving slots for future parallel sub-agents and visual, numerical, mathematical, and domain-specific review extensions.

`final_audit_report.html` 是当前老板 demo 的优先展示形态：单文件静态 HTML，突出本 case 结论、Top-N priority findings、证据定位、良性解释、人工复核动作和 role trace。Markdown 报告继续保留作为兼容输出。

`final_audit_report.html` is the primary deliverable for executive demos: a self-contained static HTML file that highlights the case verdict, Top-N priority findings, evidence anchoring, benign explanations, manual review actions, and role traces. The Markdown report is retained as a compatible fallback output.

## Web P1 数据层（web_data/）

`web_data/` 是 Web P1 工作台的 file-based 状态存储，由 `CaseStore` 管理，不依赖任何外部数据库。它与 `outputs/`（审计引擎产物目录）是两个独立的概念：

```text
web_data/
└── cases/
    └── <case_id>/
        ├── case.json        # CaseRecord：标题、状态、输入文件计数、最新 run_id
        ├── inputs/          # 用户上传的论文 PDF 和 source data 文件
        └── runs/
            └── <run_id>/
                ├── run.json       # AuditRunRecord：状态、agent_mode、workdir 路径
                └── events.jsonl   # 运行事件流（进度、日志，前端可轮询）
```

数据流关系：

| 前端操作 | API | 写入位置 |
|---|---|---|
| 创建 case | `POST /api/cases` | `web_data/cases/<id>/case.json` |
| 上传输入 | `POST /api/cases/<id>/inputs` | `web_data/cases/<id>/inputs/` |
| 启动审查 | `POST /api/cases/<id>/runs` | `web_data/cases/<id>/runs/<id>/run.json` |
| 查看产物 | `GET /api/cases/<id>/artifacts` | 读取 `outputs/`（通过 `run.workdir` 桥接） |
| 查看报告 | `GET /api/cases/<id>/report/html` | 读取 `outputs/<case_id>/.../final_audit_report.html` |

两者通过 `AuditRunRecord.workdir` 字段桥接：`web_data/` 记录"哪个 case 触发了哪次 run"，`outputs/` 存放"这次 run 产出了什么"。

## 常用命令

确定性预检查：

```bash
PYTHONPATH=. python3 cli/main.py precheck examples/bioinfo_python_case/veritas.json
```

运行轻量 manifest demo：

```bash
PYTHONPATH=. python3 cli/main.py run examples/bioinfo_python_case/veritas.json --output-dir outputs/demo
```

渲染报告：

```bash
PYTHONPATH=. python3 cli/main.py report outputs/demo/report.json --output-dir outputs/demo
```

运行论文审查 demo：

```bash
PYTHONPATH=. python3 cli/main.py audit-paper <paper_dir> --case-id <case_id> --agent-mode review --agent-timeout-seconds 180 --agent-max-retries 1 --progress plain
```

推荐先打开 `outputs/<case_id>/research-integrity-audit/final_audit_report.html` 做内部 demo。`--agent-mode full` 当前仍可能受 `agent_plan` JSON 输出不稳定影响。`audit-paper` 进度输出写入 `stderr`，最终 summary JSON 仍写入 `stdout`；需要机器消费进度时使用 `--progress jsonl`，需要安静运行时使用 `--progress off`。MinerU 子进程的 `state/pages` 输出会被转发为 `OUT mineru` 进度行。

只跑确定性链路：

```bash
PYTHONPATH=. python3 cli/main.py audit-paper <paper_dir> --case-id <case_id> --agent-mode off
```

从零重跑并禁止复用既有 MinerU 产物：

```bash
PYTHONPATH=. python3 cli/main.py audit-paper <paper_dir> --case-id <case_id> --fresh --force --agent-mode review --progress plain
```

启动 Web P1 后端（默认监听 `127.0.0.1:8765`）：

```bash
PYTHONPATH=. python3 -m web.backend.veritas_web.app
```

如果遇到 `OSError: [Errno 98] Address already in use`，说明有旧进程仍占用 8765 端口：

```bash
lsof -i :8765        # 找到占用进程的 PID
kill <PID>           # 终止旧进程后重新启动
```

启动 Web P1 前端：

```bash
cd web/frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。Vite 会把 `/api` 代理到 `http://127.0.0.1:8765`。如果先在 `web/frontend` 执行 `npm run build`，Python backend 会在 `web/frontend/dist` 存在时托管构建产物。

## 环境变量

不要把密钥写入 git。

```bash
DASHSCOPE_API_KEY=...
MINERU_API_TOKEN=...
```

`scripts/run_paper_audit.py` 默认会读取仓库根目录 `.env`，但 `.env` 必须保持未提交。

## 测试

```bash
pytest -q
```

当前 pytest 只收集本仓 `tests/`，不会扫描 `third_party/` 上游仓库测试。
