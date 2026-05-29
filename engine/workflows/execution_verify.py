from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import secrets

from engine.claims.matchers import compare_claims, load_result_map
from engine.ingest.manifest_loader import exists_file, load_manifest, path_or_none, resolve_path, resolve_repo_path
from engine.reporting.models import CheckStep, Finding, VerificationReport
from runtime.executors.base import ExecutionRequest
from runtime.executors.subprocess_executor import execute_subprocess


SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1}


def run_verification(manifest_path: str | Path, role: str = "author") -> VerificationReport:
    manifest = load_manifest(manifest_path)
    manifest_dir = Path(manifest["_manifest_dir"])
    artifacts = manifest.get("artifacts", {})
    project = manifest.get("project", {})
    claims = manifest.get("claims", [])

    paper_path = resolve_path(manifest_dir, artifacts.get("paper"))
    repo_root = resolve_path(manifest_dir, artifacts.get("repo_root"))
    results_path = resolve_path(manifest_dir, artifacts.get("results_file"))
    env_files = [resolve_path(manifest_dir, value) for value in artifacts.get("environment_files", [])]
    entrypoints = [resolve_repo_path(repo_root, value) for value in artifacts.get("entrypoints", [])]

    checks: list[CheckStep] = []
    findings: list[Finding] = []
    limitations: list[str] = []

    paper_ok = exists_file(paper_path)
    repo_ok = repo_root.is_dir()
    env_ok = any(exists_file(path) for path in env_files)
    entrypoint_ok = bool(entrypoints) and all(exists_file(path) for path in entrypoints)
    results_ok = exists_file(results_path)

    checks.append(
        CheckStep(
            key="environment",
            title="环境可重建性",
            status="pass" if env_ok else "fail",
            detail=_environment_detail(env_files),
        )
    )
    checks.append(
        CheckStep(
            key="execution",
            title="代码可执行性",
            status="pass" if entrypoint_ok else "warning",
            detail=_execution_detail(entrypoints, artifacts.get("dry_run_command")),
        )
    )

    execution_result = None
    runtime_mode = str(manifest.get("runtime", {}).get("mode", "subprocess"))
    timeout_seconds = int(manifest.get("runtime", {}).get("timeout_seconds", 30))
    if entrypoint_ok and repo_ok and artifacts.get("dry_run_command") and runtime_mode == "subprocess":
        execution_result = execute_subprocess(
            ExecutionRequest(
                command=str(artifacts["dry_run_command"]),
                workdir=repo_root,
                timeout_seconds=timeout_seconds,
            )
        )
        checks.append(
            CheckStep(
                key="runtime",
                title="本地执行验证",
                status="pass" if execution_result.success else "fail",
                detail=_runtime_detail(execution_result),
            )
        )
        if not execution_result.success:
            findings.append(
                Finding(
                    id="F-005",
                    title="dry-run 执行失败",
                    severity="critical",
                    category="runtime",
                    status="open",
                    fact=_runtime_detail(execution_result),
                    inference="当前代码仓库无法通过最小执行验证，后续 claim 审计的可信度会显著下降。",
                    suggestion="先修复 dry-run 或 smoke test，再重新运行 Veritas。",
                    source="artifacts.dry_run_command",
                    rerun_required=True,
                )
            )
    elif artifacts.get("dry_run_command") and runtime_mode != "subprocess":
        checks.append(
            CheckStep(
                key="runtime",
                title="本地执行验证",
                status="warning",
                detail=f"runtime.mode={runtime_mode} 尚未在 MVP 中实现，跳过真实执行。",
            )
        )
        limitations.append(f"Runtime mode '{runtime_mode}' is not implemented in the current MVP.")

    claim_rows: list[dict[str, object]] = []
    if results_ok and claims:
        result_map = load_result_map(results_path)
        claim_rows, claim_findings = compare_claims(claims, result_map)
        findings.extend(claim_findings)
        numerical_status = "fail" if any(item.severity == "critical" for item in claim_findings) else "pass"
        numerical_detail = f"核对 {len(claims)} 项声明，发现 {len(claim_findings)} 个问题。"
    elif claims and not results_ok:
        numerical_status = "warning"
        numerical_detail = "存在待核对 claim，但缺少 results 文件，无法完成数字一致性检查。"
        limitations.append("Results artifact missing: numerical consistency was not verified.")
    else:
        numerical_status = "warning"
        numerical_detail = "未提供结构化 claim 或 results 文件，跳过数字一致性检查。"
        limitations.append("Claim extraction is manifest-driven in this prototype; PDF parsing is not enabled yet.")

    checks.append(
        CheckStep(
            key="numerical",
            title="数字一致性",
            status=numerical_status,
            detail=numerical_detail,
        )
    )

    if not paper_ok:
        findings.append(
            Finding(
                id="F-001",
                title="缺少论文稿件",
                severity="critical",
                category="submission",
                status="open",
                fact="Manifest 未找到有效的 paper 文件路径。",
                inference="缺少论文主文档时，系统无法建立 claim 与证据的映射关系。",
                suggestion="补充 PDF、LaTeX 或 Markdown 稿件后重新核查。",
                source="artifacts.paper",
            )
        )

    if not repo_ok:
        findings.append(
            Finding(
                id="F-002",
                title="缺少代码仓库根目录",
                severity="critical",
                category="submission",
                status="open",
                fact="Manifest 指向的 repo_root 不存在或不是目录。",
                inference="没有 repo root 时，执行条件与入口文件都无法验证。",
                suggestion="补充完整仓库或修正 repo_root 路径。",
                source="artifacts.repo_root",
            )
        )

    if not env_ok:
        findings.append(
            Finding(
                id="F-003",
                title="缺少环境声明文件",
                severity="warning",
                category="environment",
                status="open",
                fact="未找到 requirements.txt、environment.yml 或等效环境文件。",
                inference="系统无法重建运行环境，只能退化为静态核查。",
                suggestion="至少提供一个环境文件，并在 manifest 中声明。",
                source="artifacts.environment_files",
            )
        )
        limitations.append("Environment rebuild was not executed because no environment file was provided.")

    if not entrypoint_ok:
        findings.append(
            Finding(
                id="F-004",
                title="缺少可执行入口声明",
                severity="warning",
                category="execution",
                status="open",
                fact="Manifest 未声明有效 entrypoint，或目标文件不存在。",
                inference="当前 demo 只能判断仓库是否齐全，无法进入 dry-run 或执行级复核。",
                suggestion="在 manifest 中补充 entrypoints，并指向仓库内真实可执行脚本。",
                source="artifacts.entrypoints",
            )
        )
        limitations.append("Execution readiness is file-based only in this prototype; commands are not run yet.")

    verification_level = _verification_level(paper_ok, repo_ok, env_ok, entrypoint_ok, results_ok, bool(claims))
    overall_status = _overall_status(findings)
    findings.sort(key=lambda item: (-SEVERITY_ORDER[item.severity], item.id))

    summary = {
        "critical": sum(1 for item in findings if item.severity == "critical"),
        "warning": sum(1 for item in findings if item.severity == "warning"),
        "info": sum(1 for item in findings if item.severity == "info"),
        "total_findings": len(findings),
        "claims_checked": len(claim_rows),
    }
    artifact_summary = {
        "paper": path_or_none(paper_path),
        "repo_root": path_or_none(repo_root),
        "environment_files": [path_or_none(path) for path in env_files],
        "entrypoints": [path_or_none(path) for path in entrypoints],
        "results_file": path_or_none(results_path),
    }
    notes = [
        "This prototype is intentionally deterministic and manifest-driven.",
        "Claim extraction still depends on manifest data; PDF-native extraction is not enabled yet.",
    ]
    if execution_result is not None:
        notes.append(f"Runtime executor: subprocess · exit_code={execution_result.exit_code}")

    return VerificationReport(
        report_id=_report_id(),
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        project_name=project.get("name", "Unnamed Project"),
        verification_level=verification_level,
        overall_status=overall_status,
        role=role,
        summary=summary,
        artifacts=artifact_summary,
        checks=checks,
        findings=findings,
        limitations=limitations,
        claim_table=claim_rows,
        notes=notes,
    )


def _environment_detail(env_files: list[Path]) -> str:
    existing = [path.name for path in env_files if path.is_file()]
    if existing:
        return f"检测到环境文件: {', '.join(existing)}。"
    return "未检测到可用环境文件。"


def _execution_detail(entrypoints: list[Path], command: str | None) -> str:
    ready = [path.name for path in entrypoints if path.is_file()]
    if ready and command:
        return f"检测到入口脚本: {', '.join(ready)}；建议 dry-run: {command}"
    if ready:
        return f"检测到入口脚本: {', '.join(ready)}。"
    return "未检测到有效入口脚本。"


def _verification_level(
    paper_ok: bool,
    repo_ok: bool,
    env_ok: bool,
    entrypoint_ok: bool,
    results_ok: bool,
    claims_ok: bool,
) -> str:
    if not paper_ok or not repo_ok:
        return "V0 Not Verifiable"
    if not env_ok:
        return "V1 Static"
    if env_ok and entrypoint_ok and results_ok and claims_ok:
        return "V3 Numerical"
    if env_ok and entrypoint_ok:
        return "V2 Executable"
    return "V1 Static"


def _overall_status(findings: list[Finding]) -> str:
    if any(item.severity == "critical" for item in findings):
        return "warning"
    if any(item.severity == "warning" for item in findings):
        return "attention"
    return "pass"


def _report_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"VRT-{stamp}-{secrets.token_hex(3).upper()}"


def _runtime_detail(result: object) -> str:
    summary = f"command='{result.command}' exit_code={result.exit_code} timeout={result.timed_out}"
    if result.stdout_tail:
        return f"{summary} stdout_tail={result.stdout_tail!r}"
    if result.stderr_tail:
        return f"{summary} stderr_tail={result.stderr_tail!r}"
    return summary
