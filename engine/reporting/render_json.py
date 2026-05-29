from __future__ import annotations

import json
from pathlib import Path

from engine.reporting.models import VerificationReport, report_from_dict


def save_report_json(report: VerificationReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_report_json(path: str | Path) -> VerificationReport:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return report_from_dict(data)
