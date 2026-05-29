from __future__ import annotations

from pathlib import Path


def scan_repo(repo_root: Path) -> dict[str, object]:
    files = [path.relative_to(repo_root).as_posix() for path in repo_root.rglob("*") if path.is_file()]
    return {
        "repo_root": str(repo_root),
        "file_count": len(files),
        "entrypoint_candidates": [name for name in files if name.endswith((".py", ".R"))][:20],
        "config_candidates": [name for name in files if name.endswith((".yml", ".yaml", ".json"))][:20],
        "result_candidates": [name for name in files if name.endswith((".csv", ".tsv"))][:20],
    }
