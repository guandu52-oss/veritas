from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["_manifest_path"] = str(manifest_path)
    data["_manifest_dir"] = str(manifest_path.parent)
    return data


def resolve_path(base_dir: Path, value: str | None) -> Path:
    if not value:
        return base_dir / "__missing__"
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def resolve_repo_path(repo_root: Path, value: str | None) -> Path:
    if not value:
        return repo_root / "__missing__"
    path = Path(value)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def path_or_none(path: Path) -> str | None:
    if str(path).endswith("__missing__"):
        return None
    return str(path)


def exists_file(path: Path) -> bool:
    return path.is_file()
