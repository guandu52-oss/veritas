from __future__ import annotations

from html import escape

from engine.reporting.models import VerificationReport


def render_html(report: VerificationReport) -> str:
    findings_html = "".join(_finding_block(item) for item in report.findings) or "<p>No findings.</p>"
    claims_html = "".join(_claim_row(row) for row in report.claim_table) or (
        "<tr><td colspan='7'>No structured claims were checked.</td></tr>"
    )
    checks_html = "".join(
        f"<li><strong>{escape(item.title)}</strong> <span class='badge {escape(item.status)}'>{escape(item.status)}</span>"
        f"<div class='muted'>{escape(item.detail)}</div></li>"
        for item in report.checks
    )
    limitations_html = "".join(f"<li>{escape(item)}</li>" for item in report.limitations) or "<li>None.</li>"
    notes_html = "".join(f"<li>{escape(item)}</li>" for item in report.notes)

    artifact_items = []
    for key, value in report.artifacts.items():
        if isinstance(value, list):
            display = ", ".join(item for item in value if item) or "N/A"
        else:
            display = value or "N/A"
        artifact_items.append(f"<li><strong>{escape(key)}</strong>: <code>{escape(display)}</code></li>")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(report.project_name)} · Verification Report</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --paper: #fffdf9;
      --ink: #2f2a22;
      --muted: #6f6657;
      --line: #ded3c1;
      --warn: #9f5e2b;
      --critical: #93422c;
      --pass: #44553b;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: linear-gradient(180deg, #f4ede2 0%, #f8f4ec 100%); color: var(--ink); font: 16px/1.6 Georgia, "Times New Roman", serif; }}
    .wrap {{ max-width: 1040px; margin: 0 auto; padding: 40px 24px 80px; }}
    .sheet {{ background: var(--paper); border: 1px solid var(--line); border-radius: 20px; padding: 32px; box-shadow: 0 14px 40px rgba(47, 42, 34, 0.08); }}
    h1, h2 {{ margin: 0 0 12px; }}
    h1 {{ font-size: 36px; line-height: 1.15; }}
    h2 {{ font-size: 22px; margin-top: 32px; }}
    .eyebrow {{ text-transform: uppercase; letter-spacing: 0.08em; font-size: 12px; color: var(--muted); margin-bottom: 12px; }}
    .meta {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 24px; margin: 24px 0; padding: 20px; border: 1px solid var(--line); border-radius: 16px; background: #fcfaf6; }}
    .summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 24px; }}
    .card {{ border: 1px solid var(--line); border-radius: 14px; padding: 16px; background: #fff; }}
    .big {{ font-size: 28px; font-weight: 700; }}
    .muted {{ color: var(--muted); font-size: 14px; }}
    ul {{ padding-left: 20px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; margin-left: 8px; border: 1px solid var(--line); }}
    .finding {{ border-left: 3px solid var(--line); padding: 16px 18px; margin: 16px 0; background: #fff; border-radius: 0 14px 14px 0; }}
    .finding.critical {{ border-color: var(--critical); }}
    .finding.warning {{ border-color: var(--warn); }}
    .finding.info {{ border-color: #8e8169; }}
    code {{ font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace; font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 12px; }}
    th, td {{ border: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ background: #faf5ec; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="sheet">
      <div class="eyebrow">Verification Report</div>
      <h1>{escape(report.project_name)}</h1>
      <p>We do not score research quality. We verify whether the paper, code, and results can be aligned with evidence.</p>
      <div class="meta">
        <div><strong>Report ID</strong><div><code>{escape(report.report_id)}</code></div></div>
        <div><strong>Generated At</strong><div><code>{escape(report.generated_at)}</code></div></div>
        <div><strong>Verification Level</strong><div>{escape(report.verification_level)}</div></div>
        <div><strong>Overall Status</strong><div>{escape(report.overall_status)}</div></div>
      </div>
      <div class="summary">
        <div class="card"><div class="big">{report.summary['total_findings']}</div><div class="muted">Total Findings</div></div>
        <div class="card"><div class="big">{report.summary['critical']}</div><div class="muted">Critical</div></div>
        <div class="card"><div class="big">{report.summary['warning']}</div><div class="muted">Warning</div></div>
        <div class="card"><div class="big">{report.summary['claims_checked']}</div><div class="muted">Claims Checked</div></div>
      </div>
      <h2>Verification Scope</h2>
      <ul>{''.join(artifact_items)}</ul>
      <h2>Checks</h2>
      <ul>{checks_html}</ul>
      <h2>Findings</h2>
      {findings_html}
      <h2>Claim Table</h2>
      <table>
        <thead><tr><th>ID</th><th>Source</th><th>Dataset</th><th>Metric</th><th>Expected</th><th>Actual</th><th>Status</th></tr></thead>
        <tbody>{claims_html}</tbody>
      </table>
      <h2>Limitations</h2>
      <ul>{limitations_html}</ul>
      <h2>Notes</h2>
      <ul>{notes_html}</ul>
    </div>
  </div>
</body>
</html>
"""


def _finding_block(item: object) -> str:
    return (
        f"<div class='finding {escape(item.severity)}'>"
        f"<strong>{escape(item.id)} · {escape(item.title)}</strong>"
        f"<div class='muted'>{escape(item.category)} · {escape(item.source)}</div>"
        f"<p><strong>Fact:</strong> {escape(item.fact)}</p>"
        f"<p><strong>Inference:</strong> {escape(item.inference)}</p>"
        f"<p><strong>Suggestion:</strong> {escape(item.suggestion)}</p>"
        f"</div>"
    )


def _claim_row(row: dict[str, object]) -> str:
    return (
        "<tr>"
        f"<td>{escape(str(row.get('id', '')))}</td>"
        f"<td>{escape(str(row.get('source', '')))}</td>"
        f"<td>{escape(str(row.get('dataset', '')))}</td>"
        f"<td>{escape(str(row.get('metric', '')))}</td>"
        f"<td>{escape(str(row.get('expected', '')))}</td>"
        f"<td>{escape(str(row.get('actual') or 'N/A'))}</td>"
        f"<td>{escape(str(row.get('status', '')))}</td>"
        "</tr>"
    )
