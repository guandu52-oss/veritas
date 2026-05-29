from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


STRUCTURED_TABLE_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}
XLSX_EXTENSIONS = {".xlsx", ".xlsm"}
RAW_DATA_EXTENSIONS = {".rds", ".rda", ".rdata", ".h5", ".hdf5", ".loom", ".mtx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}
DATA_DIR_HINTS = (
    "source data",
    "source_data",
    "source-data",
    "supplement",
    "supplementary",
    "data",
    "raw",
)


@dataclass
class MaterialFile:
    path: str
    relative_path: str
    extension: str
    size_bytes: int
    material_type: str
    directory: str


@dataclass
class OptionalLanePlan:
    lane_id: str
    status: str
    tool_ids: list[str] = field(default_factory=list)
    root: str | None = None
    reason: str = ""
    params: dict[str, Any] = field(default_factory=dict)


def build_material_inventory(paper_dir: Path, paper_pdf: Path) -> dict[str, Any]:
    files: list[MaterialFile] = []
    by_extension: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    xlsx_by_dir: dict[Path, list[Path]] = defaultdict(list)
    table_by_dir: dict[Path, list[Path]] = defaultdict(list)

    for path in sorted(item for item in paper_dir.rglob("*") if item.is_file()):
        if path == paper_pdf:
            continue
        material_type = classify_material_file(path)
        extension = path.suffix.lower()
        relative = path.relative_to(paper_dir)
        file_record = MaterialFile(
            path=str(path),
            relative_path=str(relative),
            extension=extension,
            size_bytes=path.stat().st_size,
            material_type=material_type,
            directory=str(path.parent),
        )
        files.append(file_record)
        by_extension[extension or "<none>"] += 1
        by_type[material_type] += 1
        if extension in XLSX_EXTENSIONS:
            xlsx_by_dir[path.parent].append(path)
        if extension in STRUCTURED_TABLE_EXTENSIONS:
            table_by_dir[path.parent].append(path)

    candidate_roots = source_data_roots(paper_dir, xlsx_by_dir, table_by_dir)
    supported_lanes = optional_lanes_from_inventory(candidate_roots)
    return {
        "schema_version": "1.0",
        "paper_dir": str(paper_dir),
        "paper_pdf": str(paper_pdf),
        "summary": {
            "file_count": len(files),
            "by_extension": dict(sorted(by_extension.items())),
            "by_material_type": dict(sorted(by_type.items())),
            "candidate_source_roots": len(candidate_roots),
            "supported_optional_lanes": len(supported_lanes),
        },
        "files": [asdict(item) for item in files[:500]],
        "candidate_source_roots": candidate_roots,
        "supported_optional_lanes": [asdict(item) for item in supported_lanes],
        "limitations": [
            "Inventory classification is heuristic and must be treated as material discovery, not evidence by itself.",
            "Only XLSX/XLSM Source Data lanes are executable in this MVP; CSV/TSV and raw-data lanes are inventoried but not yet executed.",
        ],
    }


def write_material_inventory(path: Path, inventory: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")


def fallback_optional_lanes(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    lanes = inventory.get("supported_optional_lanes")
    if isinstance(lanes, list) and lanes:
        return lanes
    return [
        {
            "lane_id": "source_data_xlsx",
            "status": "missing_material",
            "tool_ids": [],
            "root": None,
            "reason": "No executable XLSX/XLSM Source Data root was detected.",
            "params": {},
        }
    ]


def classify_material_file(path: Path) -> str:
    extension = path.suffix.lower()
    name = path.name.lower()
    parent_text = " ".join(part.lower() for part in path.parts[-4:])
    if extension in XLSX_EXTENSIONS:
        return "structured_table_xlsx"
    if extension in {".csv", ".tsv"}:
        return "structured_table_text"
    if extension in RAW_DATA_EXTENSIONS:
        return "raw_data"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in ARCHIVE_EXTENSIONS:
        return "archive"
    if extension == ".pdf":
        return "pdf_supplement"
    if any(hint in name or hint in parent_text for hint in DATA_DIR_HINTS):
        return "possible_data_artifact"
    return "other"


def source_data_roots(
    paper_dir: Path,
    xlsx_by_dir: dict[Path, list[Path]],
    table_by_dir: dict[Path, list[Path]],
) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    all_dirs = sorted(set(table_by_dir) | set(xlsx_by_dir))
    for directory in all_dirs:
        xlsx_files = xlsx_by_dir.get(directory, [])
        table_files = table_by_dir.get(directory, [])
        if not table_files:
            continue
        hint_score = directory_hint_score(directory, paper_dir)
        executable = bool(xlsx_files)
        confidence = "high" if executable and hint_score >= 2 else "medium" if executable else "low"
        roots.append(
            {
                "root": str(directory),
                "relative_root": str(directory.relative_to(paper_dir)) if directory.is_relative_to(paper_dir) else str(directory),
                "file_count": len(table_files),
                "xlsx_count": len(xlsx_files),
                "csv_tsv_count": len([path for path in table_files if path.suffix.lower() in {".csv", ".tsv"}]),
                "confidence": confidence,
                "executable_in_mvp": executable,
                "reason": material_root_reason(directory, executable, hint_score),
                "sample_files": [str(path.relative_to(paper_dir)) for path in table_files[:12]],
            }
        )
    return sorted(
        roots,
        key=lambda item: (
            not item["executable_in_mvp"],
            {"high": 0, "medium": 1, "low": 2}.get(str(item["confidence"]), 3),
            -int(item["file_count"]),
            str(item["relative_root"]),
        ),
    )


def optional_lanes_from_inventory(candidate_roots: list[dict[str, Any]]) -> list[OptionalLanePlan]:
    lanes: list[OptionalLanePlan] = []
    for root in candidate_roots:
        if not root.get("executable_in_mvp"):
            continue
        lanes.append(
            OptionalLanePlan(
                lane_id="source_data_xlsx",
                status="selected",
                tool_ids=["source_data.profile", "source_data.findings", "source_data.pair_forensics"],
                root=str(root["root"]),
                reason=str(root.get("reason", "XLSX Source Data root detected.")),
                params={},
            )
        )
        break
    if not lanes:
        lanes.append(
            OptionalLanePlan(
                lane_id="source_data_xlsx",
                status="missing_material",
                tool_ids=[],
                root=None,
                reason="No XLSX/XLSM Source Data root detected.",
            )
        )
    return lanes


def directory_hint_score(directory: Path, paper_dir: Path) -> int:
    try:
        relative_parts = [part.lower() for part in directory.relative_to(paper_dir).parts]
    except ValueError:
        relative_parts = [part.lower() for part in directory.parts[-4:]]
    text = " ".join(relative_parts)
    score = 0
    for hint in DATA_DIR_HINTS:
        if hint in text:
            score += 2 if hint.startswith("source") else 1
    return score


def material_root_reason(directory: Path, executable: bool, hint_score: int) -> str:
    if executable and hint_score >= 2:
        return "Directory name and XLSX/XLSM files indicate executable Source Data."
    if executable:
        return "XLSX/XLSM structured tables detected; selected as optional Source Data lane candidate."
    return "Structured table files detected, but no executable XLSX/XLSM lane is implemented in this MVP."
