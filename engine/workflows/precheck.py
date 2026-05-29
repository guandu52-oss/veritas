from __future__ import annotations

import json
from pathlib import Path

from engine.ingest.manifest_loader import exists_file, load_manifest, path_or_none, resolve_path, resolve_repo_path


def run_precheck(manifest_path: str | Path) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    manifest_dir = Path(manifest["_manifest_dir"])
    artifacts = manifest.get("artifacts", {})
    project = manifest.get("project", {})

    paper_path = resolve_path(manifest_dir, artifacts.get("paper"))
    repo_root = resolve_path(manifest_dir, artifacts.get("repo_root"))
    results_path = resolve_path(manifest_dir, artifacts.get("results_file"))
    env_files = [resolve_path(manifest_dir, value) for value in artifacts.get("environment_files", [])]
    entrypoints = [resolve_repo_path(repo_root, value) for value in artifacts.get("entrypoints", [])]

    environment_ready = any(exists_file(path) for path in env_files)
    entrypoint_ready = bool(entrypoints) and all(exists_file(path) for path in entrypoints)
    results_ready = exists_file(results_path)
    repo_ready = repo_root.is_dir()
    paper_ready = exists_file(paper_path)
    verification_level_preview = _preview_level(paper_ready, repo_ready, environment_ready, entrypoint_ready, results_ready)

    payload = {
        "project_name": project.get("name", "Unnamed Project"),
        "paper": path_or_none(paper_path),
        "repo_root": path_or_none(repo_root),
        "environment_files": [path_or_none(path) for path in env_files],
        "entrypoints": [path_or_none(path) for path in entrypoints],
        "results_file": path_or_none(results_path),
        "environment_ready": environment_ready,
        "entrypoint_ready": entrypoint_ready,
        "results_ready": results_ready,
        "repo_ready": repo_ready,
        "paper_ready": paper_ready,
        "verification_level_preview": verification_level_preview,
        "checks_passed": sum([environment_ready, entrypoint_ready, results_ready, repo_ready, paper_ready]),
        "checks_total": 5,
    }
    payload["json"] = json.dumps(payload, ensure_ascii=False, indent=2)
    return payload


def _preview_level(
    paper_ready: bool,
    repo_ready: bool,
    environment_ready: bool,
    entrypoint_ready: bool,
    results_ready: bool,
) -> str:
    if not paper_ready or not repo_ready:
        return "V0 Not Verifiable"
    if not environment_ready:
        return "V1 Static"
    if environment_ready and entrypoint_ready and results_ready:
        return "V3 Numerical"
    if environment_ready and entrypoint_ready:
        return "V2 Executable"
    return "V1 Static"
