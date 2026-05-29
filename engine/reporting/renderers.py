from __future__ import annotations

from pathlib import Path

from engine.reporting.models import VerificationReport
from engine.reporting.render_html import render_html
from engine.reporting.render_md import render_markdown


def write_reports(report: VerificationReport, output_dir: str | Path) -> dict[str, str]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = out_dir / "report.md"
    html_path = out_dir / "report.html"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return {"markdown": str(markdown_path), "html": str(html_path)}
