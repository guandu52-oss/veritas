from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from engine.static_audit.investigation import read_investigation_records

MAX_EVIDENCE_CARDS = 8
SOURCE_DATA_FINDINGS_ARTIFACT = "source_data_findings.json"
SOURCE_DATA_PAIR_FORENSICS_ARTIFACT = "source_data_pair_forensics.json"


def render_static_audit_html(workdir: Path, case_id: str) -> str:
    manifest = read_json(workdir / "audit_run_manifest.json") or {}
    bundle = read_json(workdir / "static_audit_bundle.json") or {}
    material_inventory = read_json(workdir / "material_inventory.json") or {}
    material_plan = read_json(workdir / "agent_material_plan.json") or {}
    source_findings = read_json(workdir / "source_data_findings.json") or {}
    pair_forensics = read_json(workdir / "source_data_pair_forensics.json") or {}
    source_profile = read_json(workdir / "source_data_profile.json") or {}
    numeric = read_json(workdir / "numeric_forensics.json") or {}
    ledger = read_json(workdir / "evidence_ledger.json") or {}
    exact_images = read_json(workdir / "exact_image_duplicates.json") or {}
    similarity = read_json(workdir / "image_similarity_candidates.json") or {}
    paperfraud_matches = read_json(workdir / "paperfraud_rule_matches.json") or {}
    agent_judge = read_json(workdir / "agent_judge.json") or {}
    source_auditor = read_json(workdir / "agent_source_data_auditor.json") or {}
    claim_extractor = read_json(workdir / "agent_claim_extractor.json") or {}
    investigation_records = read_investigation_records(workdir)

    primary_findings = collect_report_findings(source_findings, pair_forensics, bundle)
    mappings = source_findings.get("claim_to_source_data") or []
    canonical_claims = bundle.get("claims") or []
    canonical_mappings = bundle.get("claim_mappings") or []
    linked_mapping_by_finding = map_findings_to_mappings(mappings)
    source_reviews = map_reviews(source_auditor.get("finding_reviews") or [])
    manual_tasks = source_auditor.get("manual_review_tasks") or []
    judge_risks = agent_judge.get("risk_suggestions") or []
    traces = bundle.get("agent_traces") or []
    tool_runs = manifest.get("steps") or bundle.get("tool_runs") or []

    ledger_stats = ledger.get("stats") or {}
    material_summary = material_inventory.get("summary") or {}
    source_summary = source_findings.get("summary") or {}
    pair_summary = pair_forensics.get("summary") or {}
    profile_summary = source_profile.get("summary") or {}
    judge_summary = agent_judge.get("summary") or {}
    bundle_counts = {
        "evidence_items": len(bundle.get("evidence_items") or []),
        "claims": len(bundle.get("claims") or []),
        "findings": len(bundle.get("findings") or []),
        "claim_mappings": len(bundle.get("claim_mappings") or []),
        "agent_traces": len(traces),
    }

    evidence_clusters = build_evidence_clusters(
        primary_findings,
        source_auditor.get("claim_to_source_data") or [],
        claim_extractor.get("claims") or canonical_claims,
        manual_tasks,
        source_reviews,
        judge_risks,
    )
    cluster_cards = evidence_cluster_cards(evidence_clusters)
    pattern_findings = dedupe_findings(
        primary_findings
        + annotate_findings(
            source_findings.get("formula_derived_columns") or [],
            SOURCE_DATA_FINDINGS_ARTIFACT,
        )
    )
    patterns = build_pattern_groups(
        pattern_findings,
        source_auditor.get("claim_to_source_data") or [],
        claim_extractor.get("claims") or canonical_claims,
        manual_tasks,
        source_reviews,
        judge_risks,
    )
    pattern_cards = pattern_group_cards(patterns)
    evidence_ledger_html = irreducible_evidence_ledger(patterns)
    hero_summary = executive_summary(
        patterns,
        primary_findings,
        bundle_counts,
        profile_summary,
        exact_images,
    )
    verdict = report_verdict(primary_findings, manual_tasks, tool_runs, bundle)

    card_findings = evidence_card_findings(primary_findings)
    card_title = (
        f"原始高优先级 evidence cards（展示 {len(card_findings)} / {len(primary_findings)} 条）"
        if primary_findings
        else "重点人工复核证据卡"
    )
    cards = "\n".join(
        finding_card(
            finding,
            linked_mapping_by_finding.get(finding.get("finding_id"), []),
            source_reviews.get(finding.get("finding_id"), {}),
            risk_for_finding(judge_risks, finding.get("finding_id")),
        )
        for finding in card_findings
    ) or "<p class='muted'>未生成高优先级 finding。</p>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Veritas 静态审查 Demo · {h(case_id)}</title>
  <style>
    :root {{
      --bg: #f3efe4;
      --paper: #fffdf7;
      --ink: #20241d;
      --muted: #687064;
      --line: #d8d0bf;
      --accent: #1e5c4f;
      --accent2: #a35f26;
      --danger: #9b3d2f;
      --soft: #f8f3e8;
      --green: #dfeee7;
      --amber: #f4e1bf;
      --red: #f2d7d0;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 0%, rgba(30, 92, 79, .18), transparent 28rem),
        radial-gradient(circle at 86% 12%, rgba(163, 95, 38, .16), transparent 30rem),
        linear-gradient(180deg, #f6f0e3 0%, #ede6d7 100%);
      font: 15px/1.55 "Alegreya Sans", "Noto Serif SC", "Source Han Serif SC", Georgia, serif;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    code {{ font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace; font-size: 12px; }}
    .wrap {{ max-width: 1440px; margin: 0 auto; padding: 28px; }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.04fr) minmax(420px, .96fr);
      gap: 20px;
      align-items: stretch;
      margin-bottom: 20px;
    }}
    .panel {{
      background: rgba(255, 253, 247, .92);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 22px 60px rgba(54, 45, 28, .10);
      padding: 24px;
    }}
    .hero-brief {{
      display: flex;
      flex-direction: column;
      min-height: 560px;
      color: #fffaf0;
      background:
        radial-gradient(circle at 12% 20%, rgba(244, 225, 191, .20), transparent 18rem),
        linear-gradient(135deg, #18251f 0%, #214f45 56%, #7f4b25 140%);
      border-color: rgba(255, 250, 240, .24);
    }}
    .hero-brief .eyebrow,
    .hero-brief .muted {{
      color: rgba(255, 250, 240, .72);
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 28px;
    }}
    .meta-chip {{
      display: inline-flex;
      max-width: 100%;
      align-items: center;
      border: 1px solid rgba(255, 250, 240, .24);
      border-radius: 999px;
      padding: 5px 10px;
      color: rgba(255, 250, 240, .78);
      background: rgba(255, 250, 240, .08);
      font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
      font-size: 11px;
      overflow-wrap: anywhere;
    }}
    .verdict-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 18px;
    }}
    .verdict-badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 8px 12px;
      color: #2b1810;
      background: #f4e1bf;
      border: 1px solid rgba(244, 225, 191, .8);
      font-weight: 900;
      letter-spacing: .02em;
    }}
    .verdict-badge.outline {{
      color: rgba(255, 250, 240, .86);
      background: rgba(255, 250, 240, .06);
      border-color: rgba(255, 250, 240, .28);
    }}
    .hero-brief h1 {{
      max-width: 920px;
      color: #fffdf7;
      font-size: clamp(42px, 5.3vw, 82px);
      letter-spacing: -.055em;
    }}
    .hero-brief .lead {{
      max-width: 980px;
      color: rgba(255, 250, 240, .86);
      font-size: clamp(18px, 1.55vw, 24px);
      line-height: 1.5;
    }}
    .hero-stat-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: auto;
      padding-top: 28px;
    }}
    .hero-stat {{
      border: 1px solid rgba(255, 250, 240, .22);
      border-radius: 18px;
      padding: 14px;
      background: rgba(255, 250, 240, .08);
    }}
    .hero-stat .num {{
      color: #fffdf7;
      font-size: 32px;
      line-height: 1;
      font-weight: 900;
      letter-spacing: -.04em;
    }}
    .hero-stat .label {{
      margin-top: 8px;
      color: rgba(255, 250, 240, .68);
      font-size: 13px;
    }}
    .action-panel {{
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}
    .hero-evidence-list {{
      display: grid;
      gap: 12px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .hero-evidence-list li {{
      display: grid;
      grid-template-columns: 34px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #fffaf0;
    }}
    .evidence-kicker {{
      color: var(--accent);
      font-weight: 900;
      font-size: 13px;
    }}
    .action-list {{
      display: grid;
      gap: 8px;
      margin: 0;
      padding-left: 20px;
      color: #3f463c;
    }}
    .pattern-card {{
      border: 1px solid var(--line);
      border-radius: 30px;
      padding: 24px;
      background:
        linear-gradient(135deg, rgba(255,253,247,.96) 0%, rgba(255,247,233,.96) 100%);
      box-shadow: 0 18px 48px rgba(54, 45, 28, .08);
      margin-bottom: 18px;
      content-visibility: auto;
      contain-intrinsic-size: 360px;
    }}
    .pattern-head {{
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr) minmax(220px, .34fr);
      gap: 18px;
      align-items: start;
    }}
    .pattern-id {{
      display: grid;
      place-items: center;
      width: 58px;
      height: 58px;
      border-radius: 18px;
      color: #fffaf0;
      background: var(--accent);
      font-weight: 900;
      letter-spacing: -.03em;
    }}
    .pattern-title {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }}
    .pattern-title h3 {{
      font-size: 24px;
    }}
    .pattern-thesis {{
      font-size: 18px;
      color: #343b31;
      margin: 0;
    }}
    .pattern-facts {{
      display: grid;
      gap: 8px;
      border-left: 4px solid var(--accent);
      padding-left: 14px;
    }}
    .pattern-facts div {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid rgba(216, 208, 191, .65);
      padding-bottom: 6px;
    }}
    .pattern-actions {{
      display: grid;
      gap: 12px;
      margin-top: 18px;
    }}
    .noise-table {{
      margin-top: 12px;
      overflow-x: auto;
    }}
    .noise-cell {{
      max-width: 320px;
      overflow-wrap: anywhere;
    }}
    .eyebrow {{ color: var(--accent); font-weight: 800; letter-spacing: .08em; text-transform: uppercase; font-size: 12px; }}
    h1, h2, h3 {{ margin: 0; line-height: 1.1; }}
    h1 {{ font-size: clamp(34px, 5vw, 68px); letter-spacing: -.04em; margin-top: 10px; }}
    h2 {{ font-size: 26px; margin-bottom: 16px; }}
    h3 {{ font-size: 18px; margin-bottom: 10px; }}
    .lead {{ max-width: 900px; font-size: 19px; color: #3f463c; margin: 18px 0 0; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; gap: 16px; }}
    .cols-4 {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .cols-3 {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .cols-2 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .metric {{ border: 1px solid var(--line); border-radius: 18px; padding: 16px; background: #fffaf0; }}
    .metric .num {{ font-size: 34px; line-height: 1; font-weight: 900; letter-spacing: -.04em; }}
    .metric .label {{ color: var(--muted); margin-top: 8px; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 800;
      border: 1px solid var(--line);
      background: #fff;
      white-space: nowrap;
    }}
    .badge.high {{ background: var(--red); color: #6b1e16; border-color: #e4b4aa; }}
    .badge.medium, .badge.warning {{ background: var(--amber); color: #70430f; border-color: #e3c48d; }}
    .badge.ran, .badge.reused {{ background: var(--green); color: #214d3e; border-color: #bdd8ca; }}
    .badge.skipped {{ background: #ece7dc; color: #625a4c; }}
    .section {{ margin-top: 20px; }}
    .panel.section, .cluster-card, .compact-details, .finding-card {{
      content-visibility: auto;
      contain-intrinsic-size: 280px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 18px;
      margin-bottom: 16px;
    }}
    .quick-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 18px;
    }}
    .quick-nav a {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 7px 12px;
      background: rgba(255,255,255,.62);
      font-weight: 800;
      font-size: 13px;
    }}
    .brief-list {{
      display: grid;
      gap: 10px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .brief-list li {{
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
    }}
    .rank {{
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: var(--accent);
      color: #fffaf0;
      font-weight: 900;
      font-size: 12px;
    }}
    .cluster-card {{
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 22px;
      background: linear-gradient(135deg, #fffdf8 0%, #fff4df 100%);
      box-shadow: 0 18px 48px rgba(54, 45, 28, .08);
      margin-bottom: 16px;
    }}
    .cluster-top {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 280px;
      gap: 18px;
    }}
    .cluster-title {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
    }}
    .signal-list {{
      display: grid;
      gap: 8px;
      margin: 12px 0 0;
      padding-left: 18px;
    }}
    .compact-details > summary {{
      list-style: none;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      padding: 4px 0;
    }}
    .compact-details > summary::-webkit-details-marker {{ display: none; }}
    .appendix-grid {{
      display: grid;
      gap: 14px;
    }}
    .finding-card {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 18px;
      padding: 20px;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: linear-gradient(135deg, #fffdf8 0%, #fff7e9 100%);
      margin-bottom: 16px;
    }}
    .finding-title {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }}
    .kv {{ display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 8px 12px; font-size: 14px; }}
    .kv div:nth-child(odd) {{ color: var(--muted); }}
    .quote {{ border-left: 4px solid var(--accent); padding: 10px 12px; background: #f4f0e6; border-radius: 0 12px 12px 0; margin: 10px 0; }}
    .samples {{ display: grid; gap: 8px; margin-top: 10px; }}
    .sample-row {{ display: grid; grid-template-columns: 52px 1fr 1fr; gap: 8px; font-family: "JetBrains Mono", monospace; font-size: 12px; }}
    .lane {{ padding: 10px; border: 1px solid var(--line); border-radius: 14px; background: rgba(255,255,255,.72); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .artifact-list {{ display: grid; gap: 8px; }}
    .artifact {{ display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }}
    details {{ border: 1px solid var(--line); border-radius: 16px; padding: 12px 14px; background: rgba(255,255,255,.65); }}
    summary {{ cursor: pointer; font-weight: 800; }}
    .footer {{ margin: 24px 0 8px; color: var(--muted); text-align: center; }}
    @media (max-width: 980px) {{
      .hero, .finding-card, .cluster-top, .pattern-head, .hero-stat-grid, .cols-4, .cols-3, .cols-2 {{ grid-template-columns: 1fr; }}
      .hero-brief {{ min-height: auto; }}
      .section-head {{ align-items: flex-start; flex-direction: column; }}
      .wrap {{ padding: 14px; }}
      .panel {{ padding: 18px; border-radius: 18px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="panel hero-brief">
        <div class="hero-meta">
          <span class="eyebrow">Veritas 静态审查 Demo</span>
          <span class="meta-chip">case_id: {h(case_id)}</span>
          <span class="meta-chip">static audit only</span>
        </div>
        <div class="verdict-row">
          <span class="verdict-badge">{h(verdict["label"])}</span>
          <span class="verdict-badge outline">非科研诚信定论</span>
          <span class="verdict-badge outline">{h(verdict["depth"])}</span>
        </div>
        <h1>投稿前技术复核：<br/>{h(verdict["headline"])}</h1>
        <p class="lead">{h(hero_summary)}</p>
        <div class="hero-stat-grid">
          {hero_metric("可复核规律", len(patterns))}
          {hero_metric("高优先级 findings", len(primary_findings))}
          {hero_metric("claim 映射", bundle_counts["claim_mappings"])}
          {hero_metric("Source coverage", source_coverage_value(profile_summary))}
        </div>
      </div>
      <aside class="panel action-panel">
        <div>
          <div class="eyebrow">First read</div>
          <h2>{h("先看 Top Patterns" if patterns else "先看覆盖范围")}</h2>
          <p class="muted">规律只描述一次；每条不可约证据记录保留原始定位和值，放在折叠区供复查。</p>
        </div>
        {hero_pattern_list(patterns)}
        <div>
          <h3>下一步动作</h3>
          {hero_action_list(manual_tasks)}
        </div>
        <nav class="quick-nav" aria-label="report shortcuts">
          <a href="#top-patterns">Top Patterns</a>
          <a href="#noise-ledger">Evidence Ledger</a>
          <a href="#claim-impact">Claim Impact</a>
          <a href="#manual-review">人工复核</a>
          <a href="#appendix">技术附录</a>
        </nav>
      </aside>
    </section>

    <section class="section" id="top-patterns">
      <div class="section-head">
        <div>
          <h2>Top Patterns</h2>
          <p class="muted">这是压缩后的规律层。相同规律只讲一次；展开后才进入不可约噪声层。</p>
        </div>
        <span class="badge high">{h(len(patterns))} patterns</span>
      </div>
      {pattern_cards}
    </section>

    <section class="panel section" id="noise-ledger">
      <h2>不可约 Evidence Ledger</h2>
      <p class="muted">这里不再压缩叙事，只保留每条证据必须原样记录的字段：finding_id、来源 artifact、定位、support、样本值、公式或摘要。</p>
      {evidence_ledger_html}
    </section>

    <section class="panel section" id="claim-impact">
      <h2>Claim Impact Matrix</h2>
      <p class="muted">把 Agent 抽取的 claim、evidence 引用和 finding 合在一张表里，帮助作者或 PI 判断哪些论文表述需要优先复核。</p>
      {claim_impact_matrix(source_auditor.get("claim_to_source_data") or [], claim_extractor.get("claims") or canonical_claims, canonical_mappings)}
    </section>

    <section class="panel section" id="manual-review">
      <h2>人工复核清单</h2>
      <p class="muted">这些问题是报告的行动入口。Veritas 不替代人工判断，而是把最值得核对的 workbook、sheet、row/column 和 claim 收敛出来。</p>
      {manual_tasks_table(manual_tasks)}
    </section>

    <section class="panel section" id="paperfraud-rules">
      {paperfraud_rule_section(paperfraud_matches)}
    </section>

    <section class="panel section" id="coverage">
      <h2>覆盖范围与限制</h2>
      <div class="grid cols-4">
        {metric("evidence ledger 条目", ledger_stats.get("ledger_items", "-"))}
        {metric("数值单元格", profile_summary.get("numeric_cell_count", "-"))}
        {metric("公式单元格", profile_summary.get("formula_count", "-"))}
        {metric("Agent traces", bundle_counts["agent_traces"])}
      </div>
      <ul>
        {list_items(collect_limitations(bundle, agent_judge, similarity))}
      </ul>
    </section>

    <section class="section" id="appendix">
      <h2>技术附录</h2>
      <div class="appendix-grid">
        <details class="compact-details">
          <summary><span><strong>材料发现与 Optional Lane</strong><br/><span class="muted">输入材料、Agent material plan 和可执行数据 lane。</span></span><span class="badge skipped">展开</span></summary>
          {material_plan_panel(material_summary, material_plan)}
        </details>

        <details class="compact-details">
          <summary><span><strong>Agent Investigation Path</strong><br/><span class="muted">Agent 选择 Tool Registry 中确定性工具后的调查轨迹。</span></span><span class="badge skipped">展开</span></summary>
          <p class="muted">Agent 只负责提出 hypothesis 和选择 Tool Registry 允许的确定性工具；orchestrator 负责校验、执行和记录 artifact。</p>
          {investigation_table(investigation_records)}
        </details>

        <details class="compact-details">
          <summary><span><strong>Agent 精炼 Claim-to-Evidence 主视图</strong><br/><span class="muted">完整 claim mapping 和 deterministic scaffolding。</span></span><span class="badge skipped">展开</span></summary>
          <div class="grid cols-3">
            {metric("canonical claims", len(canonical_claims))}
            {metric("canonical mappings", len(canonical_mappings))}
            {metric("deterministic scaffolding", len(mappings))}
          </div>
          {canonical_mapping_table(canonical_claims, canonical_mappings)}
        </details>

        <details class="compact-details">
          <summary><span><strong>Source / Evidence Clusters</strong><br/><span class="muted">旧阅读维度：按来源和定位聚类，作为 Pattern-first 视图的回退索引。</span></span><span class="badge skipped">展开</span></summary>
          {cluster_cards}
        </details>

        <details class="compact-details">
          <summary><span><strong>{h(card_title)}</strong><br/><span class="muted">单条 finding 级证据卡，保留给技术复查使用。</span></span><span class="badge skipped">展开</span></summary>
          {cards}
        </details>

        <details class="compact-details">
          <summary><span><strong>Source Data Pair / Row-Offset Forensics</strong><br/><span class="muted">paired cohort、固定行偏移、低宽度行重复和比例复用模式。</span></span><span class="badge skipped">展开</span></summary>
          <div class="grid cols-4">
            {metric("priority findings", pair_summary.get("priority_findings", 0))}
            {metric("row-offset scalar", pair_summary.get("row_offset_scalar_findings", 0))}
            {metric("paired ratio reuse", pair_summary.get("paired_ratio_reuse_findings", 0))}
            {metric("duplicate row vector", pair_summary.get("duplicate_row_vector_findings", 0))}
          </div>
          {pair_forensics_table(pair_forensics.get("priority_findings") or [])}
        </details>

        <details class="compact-details">
          <summary><span><strong>Pipeline 与 Agent role trace</strong><br/><span class="muted">运行步骤、状态、Agent role 输出路径。</span></span><span class="badge skipped">展开</span></summary>
          <div class="grid cols-2">
            <div>
              <h3>执行状态</h3>
              {steps_table(tool_runs)}
            </div>
            <div>
              <h3>Agent role 层</h3>
              {traces_table(traces)}
            </div>
          </div>
        </details>

        <details class="compact-details">
          <summary><span><strong>确定性检查摘要</strong><br/><span class="muted">Source Data、PDF 数字取证和图像检查的原始摘要。</span></span><span class="badge skipped">展开</span></summary>
          <div class="grid cols-3">
            <div class="lane">
              <h3>Source Data</h3>
              <div class="kv">
                <div>workbook 数</div><div>{h(profile_summary.get("workbook_count", "-"))}</div>
                <div>sheet 数</div><div>{h(profile_summary.get("sheet_count", "-"))}</div>
                <div>重复列 finding</div><div>{h(source_summary.get("duplicate_column_findings", "-"))}</div>
                <div>固定关系 finding</div><div>{h(source_summary.get("fixed_relationship_findings", "-"))}</div>
                <div>错误数</div><div>{h(source_summary.get("errors", "-"))}</div>
              </div>
            </div>
            <div class="lane">
              <h3>PDF 数字取证</h3>
              <div class="kv">
                <div>提取数字数</div><div>{h(numeric.get("all_number_count", "-"))}</div>
                <div>有效数字数</div><div>{h(numeric.get("number_count", "-"))}</div>
                <div>表格数</div><div>{h(numeric.get("table_count", "-"))}</div>
                <div>Benford MAD</div><div>{h((numeric.get("benford") or {}).get("mad", (numeric.get("benford") or {}).get("mean_absolute_deviation", "-")))}</div>
              </div>
            </div>
            <div class="lane">
              <h3>图像检查</h3>
              <div class="kv">
                <div>图片数</div><div>{h(exact_images.get("image_count", "-"))}</div>
                <div>字节级重复组</div><div>{h(exact_images.get("duplicate_group_count", "-"))}</div>
                <div>近似重复状态</div><div>{h(status_label(similarity.get("status", "-")))}</div>
                <div>方法</div><div>{h(similarity.get("method", "-"))}</div>
              </div>
            </div>
          </div>
        </details>

        <details class="compact-details">
          <summary><span><strong>产物链接与 Agent 风险建议</strong><br/><span class="muted">原始 JSON/Markdown artifact 和 role 级风险建议。</span></span><span class="badge skipped">展开</span></summary>
          {artifact_links(workdir)}
          <h3>JudgeAgent 完整摘要</h3>
          <p>{h(judge_summary.get("technical_risk_summary", "JudgeAgent 未生成摘要。"))}</p>
          <h3>JudgeAgent 风险建议摘要</h3>
          {risks_table(judge_risks)}
          <h3>ClaimExtractor 摘要</h3>
          <p>claim 数：{h(len(claim_extractor.get("claims") or []))}；限制说明数：{h(len(claim_extractor.get("limitations") or []))}</p>
        </details>
      </div>
    </section>
    <div class="footer">生成时间：{h(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))}。证据优先，Agent 辅助解释，关键结论必须人工复核。</div>
  </main>
</body>
</html>
"""


def write_static_audit_html(workdir: Path, case_id: str) -> Path:
    path = workdir / "final_audit_report.html"
    path.write_text(render_static_audit_html(workdir, case_id), encoding="utf-8")
    return path


def paperfraud_rule_section(artifact: dict[str, Any]) -> str:
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    triggered = [item for item in (artifact.get("triggered_rules") or []) if isinstance(item, dict)]
    rows = []
    for item in triggered[:12]:
        rows.append(
            "<tr>"
            f"<td><code>{h(item.get('rule_id', '-'))}</code></td>"
            f"<td>{h(item.get('severity', '-'))}</td>"
            f"<td>{h(item.get('rule_type', '-'))}</td>"
            f"<td>{h(item.get('title', '-'))}</td>"
            f"<td>{h(item.get('evidence', '-'))}</td>"
            f"<td>{h(item.get('human_review', '-'))}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>rule_id</th><th>severity</th><th>type</th><th>title</th><th>evidence</th><th>human review</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        if rows
        else "<p class='muted'>未命中 PaperFraud 规则库提示项，或尚未生成 paperfraud_rule_matches.json。</p>"
    )
    return f"""
      <h2>PaperFraud 规则库命中</h2>
      <p class="muted">这些命中是方法学和 fraud-pattern reviewer prompts，只用于收敛人工复核问题，不构成最终学术不端判定。</p>
      <div class="grid cols-4">
        {metric("规则数", summary.get("total_rules_loaded", "-"))}
        {metric("命中数", summary.get("total_triggered", "-"))}
        {metric("methodology", summary.get("methodology_review_triggered", "-"))}
        {metric("fraud-pattern", summary.get("fraud_detection_triggered", "-"))}
      </div>
      {table}
    """


def collect_report_findings(
    source_findings: dict[str, Any],
    pair_forensics: dict[str, Any],
    bundle: dict[str, Any],
) -> list[dict[str, Any]]:
    findings = []
    findings.extend(
        annotate_findings(
            source_findings.get("priority_findings") or [],
            SOURCE_DATA_FINDINGS_ARTIFACT,
        )
    )
    findings.extend(
        annotate_findings(
            pair_forensics.get("priority_findings") or [],
            SOURCE_DATA_PAIR_FORENSICS_ARTIFACT,
        )
    )

    seen = {str(finding.get("finding_id")) for finding in findings if finding.get("finding_id")}
    for item in bundle.get("findings") or []:
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id") or "")
        if finding_id and finding_id in seen:
            continue
        normalized = normalize_bundle_finding(item, bundle)
        if finding_id:
            seen.add(finding_id)
        findings.append(normalized)
    return sorted(
        dedupe_findings(findings),
        key=lambda finding: (
            -risk_score(finding.get("risk_level")),
            -finding_support_value(finding),
            str(finding.get("source_artifact", "")),
            str(finding.get("finding_id", "")),
        ),
    )


def annotate_findings(findings: list[dict[str, Any]], source_artifact: str) -> list[dict[str, Any]]:
    annotated = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        item = dict(finding)
        item.setdefault("source_artifact", source_artifact)
        annotated.append(item)
    return annotated


def normalize_bundle_finding(item: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    finding = dict(metadata)
    for key in (
        "finding_id",
        "category",
        "risk_level",
        "summary",
        "evidence_refs",
        "claim_refs",
        "benign_explanations",
        "pressure_test_result",
        "manual_review_note",
    ):
        if item.get(key) not in (None, "", []):
            finding[key] = item.get(key)
    finding.setdefault("source_artifact", metadata.get("source_artifact") or "static_audit_bundle.json")
    if not finding.get("source_path"):
        finding["source_path"] = source_path_for_evidence_refs(finding.get("evidence_refs") or [], bundle)
    return finding


def source_path_for_evidence_refs(evidence_refs: list[Any], bundle: dict[str, Any]) -> str:
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in (bundle.get("evidence_items") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    paths = []
    for ref in evidence_refs:
        evidence = evidence_by_id.get(str(ref))
        if evidence and evidence.get("source_path"):
            paths.append(str(evidence.get("source_path")))
    return ", ".join(dedupe(paths)[:3])


def report_verdict(
    findings: list[dict[str, Any]],
    manual_tasks: list[dict[str, Any]],
    tool_runs: list[dict[str, Any]],
    bundle: dict[str, Any],
) -> dict[str, str]:
    statuses = {str(step.get("status")) for step in tool_runs if isinstance(step, dict)}
    max_risk = max((risk_score(finding.get("risk_level")) for finding in findings), default=0)
    has_review_work = bool(findings or manual_tasks)
    has_failed_tool = "failed" in statuses
    has_warning_tool = "warning" in statuses

    if max_risk >= risk_score("critical"):
        label = "High Technical Conflict"
        headline = "存在高强度技术冲突"
        result = "fail"
    elif has_review_work or has_failed_tool or has_warning_tool:
        label = "Needs Human Review"
        headline = "需人工复核"
        result = "warning"
    else:
        label = "No Priority Findings"
        headline = "未见高优先级自动 finding"
        result = "pass"

    return {
        "label": label,
        "headline": headline,
        "result": result,
        "depth": audit_depth_label(bundle, tool_runs),
    }


def audit_depth_label(bundle: dict[str, Any], tool_runs: list[dict[str, Any]]) -> str:
    step_keys = {str(step.get("key") or step.get("step_key")) for step in tool_runs if isinstance(step, dict)}
    evidence_count = len(bundle.get("evidence_items") or [])
    claim_mapping_count = len(bundle.get("claim_mappings") or [])
    execution_status = (bundle.get("execution_status") or {}).get("status")
    if not evidence_count and not step_keys:
        return "V0 coverage"
    if execution_status == "ran":
        return "V4 coverage"
    if claim_mapping_count or len(bundle.get("agent_traces") or []):
        return "V3 coverage"
    if {"source_data_profile", "source_data_findings", "source_data_pair_forensics", "exact_image_duplicates"} & step_keys:
        return "V2 coverage"
    return "V1 coverage"


def executive_summary(
    patterns: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    bundle_counts: dict[str, int],
    profile_summary: dict[str, Any],
    exact_images: dict[str, Any],
) -> str:
    pattern_titles = [str(pattern.get("title")) for pattern in patterns[:3] if pattern.get("title")]
    source_coverage = source_coverage_text(profile_summary)
    image_count = exact_images.get("image_count", "-")
    if not findings:
        return (
            "本次静态技术复核未生成高优先级自动 finding。"
            f"当前 {source_coverage}、"
            f"{bundle_counts.get('claim_mappings', 0)} 条 claim 映射和 {image_count} 张 PDF 提取图片；"
            "仍需结合材料完整性和人工抽查确认。"
        )
    pattern_text = "、".join(pattern_titles) if pattern_titles else "技术异常候选"
    return (
        f"本次静态技术复核把 {len(findings)} 条高优先级技术异常候选压缩为 {len(patterns)} 类可复核规律："
        f"{pattern_text}。当前 {source_coverage}、"
        f"{bundle_counts.get('claim_mappings', 0)} 条 claim 映射和 {image_count} 张 PDF 提取图片；"
        "结论仅表示需要人工复核，不构成科研诚信判定。"
    )


def source_coverage_value(profile_summary: dict[str, Any]) -> str:
    workbook_count = profile_summary.get("workbook_count")
    sheet_count = profile_summary.get("sheet_count")
    if workbook_count is None and sheet_count is None:
        return "not selected"
    return f"{workbook_count if workbook_count is not None else '-'} / {sheet_count if sheet_count is not None else '-'}"


def source_coverage_text(profile_summary: dict[str, Any]) -> str:
    workbook_count = profile_summary.get("workbook_count")
    sheet_count = profile_summary.get("sheet_count")
    if workbook_count is None and sheet_count is None:
        return "未形成 Source Data workbook/sheet 覆盖指标"
    return f"已覆盖 {workbook_count if workbook_count is not None else '-'} 个 workbook / {sheet_count if sheet_count is not None else '-'} 个 sheet"


def hero_pattern_list(patterns: list[dict[str, Any]]) -> str:
    if not patterns:
        return "<p class='muted'>未生成 Top Patterns。请查看技术附录中的原始工具输出。</p>"
    rows = []
    for index, pattern in enumerate(patterns[:3], start=1):
        rows.append(
            "<li>"
            f"<span class='rank'>{h(index)}</span>"
            "<span>"
            f"<span class='evidence-kicker'>{h(pattern.get('title'))}</span><br/>"
            f"{h(pattern.get('thesis'))}"
            "</span>"
            "</li>"
        )
    return "<ol class='hero-evidence-list'>" + "".join(rows) + "</ol>"


def hero_action_list(tasks: list[dict[str, Any]]) -> str:
    questions = [
        shorten(str(task.get("question", "")), 150)
        for task in tasks[:3]
        if isinstance(task, dict) and task.get("question")
    ]
    if not questions:
        questions = [
            "核对材料清单、PDF 解析、Source Data、图像和代码材料是否完整。",
            "要求作者补充缺失的原始数据、导出过程、分析脚本或结果文件。",
            "把后续生成的 finding 与论文 claim 逐条对账，确认是否需要补充材料或说明。",
        ]
    return "<ul class='action-list'>" + list_items(questions) + "</ul>"


def hero_metric(label: str, value: Any) -> str:
    return f"<div class='hero-stat'><div class='num'>{h(value)}</div><div class='label'>{h(label)}</div></div>"


def key_sheets(clusters: list[dict[str, Any]], limit: int) -> list[str]:
    result = []
    for cluster in clusters:
        sheet = str(cluster.get("sheet") or "")
        if sheet and sheet not in result:
            result.append(sheet)
        if len(result) >= limit:
            break
    return result


def build_pattern_groups(
    findings: list[dict[str, Any]],
    claim_mappings: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    manual_tasks: list[dict[str, Any]],
    source_reviews: dict[str, dict[str, Any]],
    judge_risks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        if isinstance(finding, dict):
            grouped[pattern_key_for_finding(finding)].append(finding)

    patterns = []
    for index, (pattern_key, group_findings) in enumerate(sorted(grouped.items(), key=pattern_sort_key), start=1):
        group_findings = sorted(
            group_findings,
            key=lambda finding: (
                -risk_score(finding.get("risk_level")),
                str(finding.get("sheet", "")),
                str(finding.get("finding_id", "")),
            ),
        )
        definition = pattern_definition(pattern_key)
        finding_ids = [str(finding.get("finding_id")) for finding in group_findings if finding.get("finding_id")]
        matched_claims = claims_for_finding_ids(finding_ids, claims, claim_mappings)
        matched_tasks = tasks_for_finding_ids(finding_ids, manual_tasks)
        matched_risks = [risk for risk in judge_risks if any(ref_mentions_finding(ref, finding_ids) for ref in (risk.get("evidence_refs") or []))]
        reviews = [source_reviews[finding_id] for finding_id in finding_ids if finding_id in source_reviews]
        sheets = sorted({str(finding.get("sheet")) for finding in group_findings if finding.get("sheet")})
        workbooks = sorted({str(finding.get("workbook")) for finding in group_findings if finding.get("workbook")})
        categories = Counter(str(finding.get("category", "-")) for finding in group_findings)
        risk = max((str(finding.get("risk_level", "medium")) for finding in group_findings), key=risk_score, default="medium")
        patterns.append(
            {
                "pattern_id": f"P-{index:03d}",
                "pattern_key": pattern_key,
                "title": definition["title"],
                "thesis": definition["thesis"],
                "review_question": definition["review_question"],
                "risk_level": risk,
                "findings": group_findings,
                "finding_ids": finding_ids,
                "sheets": sheets,
                "workbooks": workbooks,
                "categories": categories,
                "claims": matched_claims,
                "manual_tasks": matched_tasks,
                "risks": matched_risks,
                "reviews": reviews,
                "benign_explanations": cluster_benign_explanations(group_findings, reviews),
            }
        )
    return patterns


def pattern_group_cards(patterns: list[dict[str, Any]]) -> str:
    if not patterns:
        return "<p class='muted'>未形成可复核规律。请查看不可约 Evidence Ledger。</p>"
    cards = []
    for pattern in patterns:
        claims = pattern.get("claims") or []
        claim_items = [
            f"<li><code>{h(claim.get('claim_id', '-'))}</code> {h(shorten(claim.get('claim_text') or claim.get('text') or '-', 220))}</li>"
            for claim in claims[:5]
        ] or ["<li class='muted'>未自动关联到具体 claim，需人工补映射。</li>"]
        task_items = [
            shorten(str(task.get("question", "")), 180)
            for task in (pattern.get("manual_tasks") or [])[:3]
            if isinstance(task, dict) and task.get("question")
        ] or [str(pattern.get("review_question", "请核对该规律是否有合法数据处理来源。"))]
        categories = pattern.get("categories") or Counter()
        cards.append(
            f"""
<article class="pattern-card" id="{h(pattern.get('pattern_id'))}">
  <div class="pattern-head">
    <div class="pattern-id">{h(pattern.get('pattern_id'))}</div>
    <div>
      <div class="pattern-title">
        <span class="badge {h(pattern.get('risk_level'))}">{h(risk_label(pattern.get('risk_level')))}</span>
        <h3>{h(pattern.get('title'))}</h3>
      </div>
      <p class="pattern-thesis">{h(pattern.get('thesis'))}</p>
    </div>
    <aside class="pattern-facts">
      <div><span class="muted">evidence records</span><strong>{h(len(pattern.get('findings') or []))}</strong></div>
      <div><span class="muted">sheets</span><strong>{h(len(pattern.get('sheets') or []))}</strong></div>
      <div><span class="muted">claims</span><strong>{h(len(claims))}</strong></div>
    </aside>
  </div>
  <div class="grid cols-2 pattern-actions">
    <div>
      <h3>规律出现在哪里</h3>
      <p>{h(', '.join(pattern.get('sheets') or []) or '-')}</p>
      <p class="muted">{h(', '.join(f'{category_label(key)}×{value}' for key, value in categories.most_common()) or '-')}</p>
    </div>
    <div>
      <h3>人工复核问题</h3>
      <ul>{list_items(task_items)}</ul>
    </div>
  </div>
  <details class="section">
    <summary>展开：影响的 claim</summary>
    <ul>{''.join(claim_items)}</ul>
  </details>
  <details class="section">
    <summary>展开：可能良性解释</summary>
    <ul>{list_items(pattern.get("benign_explanations") or [])}</ul>
  </details>
  <details class="section">
    <summary>展开：不可约证据记录</summary>
    {evidence_records_table(pattern.get("findings") or [], compact=True)}
  </details>
</article>
"""
        )
    return "\n".join(cards)


def irreducible_evidence_ledger(patterns: list[dict[str, Any]]) -> str:
    if not patterns:
        return "<p class='muted'>未生成不可约证据记录。</p>"
    sections = []
    for pattern in patterns:
        sections.append(
            f"""
<details class="compact-details">
  <summary><span><strong>{h(pattern.get('pattern_id'))} · {h(pattern.get('title'))}</strong><br/><span class="muted">{h(len(pattern.get('findings') or []))} records · {h(', '.join(pattern.get('sheets') or []) or '-')}</span></span><span class="badge skipped">展开</span></summary>
  {evidence_records_table(pattern.get("findings") or [])}
</details>
"""
        )
    return "<div class='appendix-grid'>" + "\n".join(sections) + "</div>"


def evidence_records_table(findings: list[dict[str, Any]], compact: bool = False) -> str:
    if not findings:
        return "<p class='muted'>该规律下没有可展示证据记录。</p>"
    rows = []
    for finding in findings:
        rows.append(
            "<tr>"
            f"<td><code>{h(finding.get('finding_id', '-'))}</code></td>"
            f"<td>{h(category_label(finding.get('category', '-')))}</td>"
            f"<td class='noise-cell'><code>{h(evidence_source_text(finding))}</code></td>"
            f"<td class='noise-cell'><code>{h(evidence_locator(finding))}</code></td>"
            f"<td>{h(support_text(finding))}</td>"
            f"<td class='noise-cell'>{h(evidence_sample_text(finding, limit=1 if compact else 3))}</td>"
            "</tr>"
        )
    return (
        "<div class='noise-table'><table><thead><tr><th>finding</th><th>category</th><th>source</th><th>locator</th><th>support</th><th>sample / formula / summary</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def evidence_source_text(finding: dict[str, Any]) -> str:
    workbook = finding.get("workbook")
    sheet = finding.get("sheet")
    if workbook or sheet:
        return " / ".join(str(item) for item in (workbook, sheet) if item)
    for key in ("source_path", "image_path", "figure_path", "artifact_path", "source_artifact"):
        if finding.get(key):
            return str(finding.get(key))
    refs = finding.get("evidence_refs") or []
    if refs:
        return ", ".join(str(ref) for ref in refs[:3])
    return "-"


def evidence_locator(finding: dict[str, Any]) -> str:
    columns = finding.get("columns") or finding.get("column_pair") or finding.get("target_column") or finding.get("column") or []
    if isinstance(columns, list):
        columns_text = ",".join(str(item) for item in columns)
    else:
        columns_text = str(columns)
    parts = []
    if columns_text:
        parts.append(f"cols={columns_text}")
    if finding.get("row_offset") is not None:
        parts.append(f"row_offset={finding.get('row_offset')}")
    if finding.get("pair_id_offset") is not None:
        parts.append(f"pair_id_offset={finding.get('pair_id_offset')}")
    if finding.get("target_column_label"):
        parts.append(f"label={finding.get('target_column_label')}")
    if finding.get("dominant_formula_pattern"):
        parts.append(f"formula={finding.get('dominant_formula_pattern')}")
    for key in ("figure", "panel_id", "bbox", "page", "line", "cell", "range"):
        if finding.get(key):
            parts.append(f"{key}={finding.get(key)}")
    return "; ".join(parts) or "-"


def evidence_sample_text(finding: dict[str, Any], limit: int = 3) -> str:
    formulas = finding.get("sample_formulas") or []
    if formulas:
        samples = []
        for item in formulas[:limit]:
            if isinstance(item, dict):
                samples.append(f"{item.get('ref', '-')}: {item.get('formula', '-')}")
        return "; ".join(samples) or "-"
    pairs = finding.get("sample_pairs") or []
    if pairs:
        samples = []
        for item in pairs[:limit]:
            if isinstance(item, dict):
                samples.append(f"row {item.get('row', '-')}: {item.get('left', '-')} -> {item.get('right', '-')}")
        return "; ".join(samples) or "-"
    for key in ("sample_rows", "examples", "sample_values"):
        values = finding.get(key) or []
        if values:
            return shorten(json.dumps(values[:limit], ensure_ascii=False), 220)
    if finding.get("dominant_formula_support"):
        return f"{finding.get('dominant_formula_pattern', '-')} ({finding.get('dominant_formula_support')})"
    if finding.get("summary"):
        return shorten(str(finding.get("summary")), 220)
    return "-"


def pattern_sort_key(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, int, str]:
    key, findings = item
    order = {
        "paired_offset_ratio_reuse": 0,
        "row_vector_reuse_rounding": 1,
        "formula_derivation": 2,
        "visual_forensics": 3,
        "numeric_forensics": 4,
        "execution_evidence": 5,
        "other": 9,
    }
    return (order.get(key, 7), -len(findings), key)


def pattern_key_for_finding(finding: dict[str, Any]) -> str:
    category = str(finding.get("category", ""))
    source_artifact = str(finding.get("source_artifact", ""))
    if category in {"row_offset_scalar_multiple", "long_format_paired_ratio_reuse", "long_format_within_pair_ratio_enrichment"}:
        return "paired_offset_ratio_reuse"
    if category in {"duplicate_row_vector", "row_offset_partial_copy_rounding_bias", "duplicate_numeric_columns"}:
        return "row_vector_reuse_rounding"
    if category in {"formula_derived_column", "formula_derived_columns", "fixed_ratio", "fixed_difference"}:
        return "formula_derivation"
    category_text = category.lower()
    source_text = source_artifact.lower()
    if any(token in category_text or token in source_text for token in ("image", "visual", "panel", "trufor", "copy_move", "cbir", "similarity")):
        return "visual_forensics"
    if any(token in category_text or token in source_text for token in ("numeric", "benford", "number")):
        return "numeric_forensics"
    if any(token in category_text or token in source_text for token in ("execution", "command", "runtime")):
        return "execution_evidence"
    if category:
        return f"category:{category}"
    return "other"


def pattern_definition(pattern_key: str) -> dict[str, str]:
    definitions = {
        "paired_offset_ratio_reuse": {
            "title": "配对样本固定行偏移与比例复用",
            "thesis": "多个 Source Data sheet 中，配对样本在固定行偏移后反复出现标量关系或两组比例复用；规律只在这里描述一次，具体 sheet/行/列作为证据记录保留。",
            "review_question": "确认这些固定偏移和比例复用是否来自合法配对排序、归一化分母或批量派生，而不是同一数据的重复改写。",
        },
        "row_vector_reuse_rounding": {
            "title": "低维行向量重复与舍入偏差",
            "thesis": "若干 figure 的 Source Data 出现行向量重复、部分复制或四舍五入偏差；该规律可能是模板行、censoring 行或真实重复，也可能提示需要追溯导出过程。",
            "review_question": "核对重复行是否有实验或统计语义，例如 censoring 模板、分组标签、重复测量，或导出时批量复制。",
        },
        "formula_derivation": {
            "title": "公式派生列与固定倍数转换",
            "thesis": "部分列由相邻单元格或同列历史值按固定公式派生。公式本身不是异常，但它会改变 claim 对“原始测量值”的可追溯性。",
            "review_question": "确认论文图表引用的是原始测量值还是派生值，并要求作者说明公式来源、单位换算或归一化逻辑。",
        },
        "visual_forensics": {
            "title": "视觉证据相似或复用候选",
            "thesis": "视觉工具生成了需要人工确认的图像、panel、相似关系或区域级候选；这些信号只能作为复核入口，不能直接作为诚信结论。",
            "review_question": "核对原图、panel、caption、相似方法、分数和最强良性解释，确认是否对应同一主体、合法复用或导出伪影。",
        },
        "numeric_forensics": {
            "title": "PDF 数字取证候选",
            "thesis": "PDF 或表格数字检查生成了统计线索；需要排除 OCR、表格解析、四舍五入和展示层转写造成的伪影。",
            "review_question": "回到原始表格、Source Data 或结果文件，确认数字关系是否能由原始数据和统计流程解释。",
        },
        "execution_evidence": {
            "title": "执行证据与 claim 对账候选",
            "thesis": "运行命令、日志或结果文件与论文 claim 之间存在待核对项；该类 finding 需要回到 runtime manifest 和输出产物验证。",
            "review_question": "核对命令、环境、stdout/stderr、exit code、结果文件 hash 和 claim 映射是否一致。",
        },
        "other": {
            "title": "其他未归类技术异常",
            "thesis": "这些证据尚未被归入稳定领域规律，需保留原始记录后人工判断。",
            "review_question": "逐条核对 finding 的数据语义、生成过程和论文 claim 影响。",
        },
    }
    if pattern_key.startswith("category:"):
        category = pattern_key.split(":", 1)[1]
        label = category_label(category)
        return {
            "title": label,
            "thesis": f"{label} 生成了可复核技术事实候选；报告保留原始定位和证据引用，避免把未知类别压扁成单一 demo 叙事。",
            "review_question": "回到原始 artifact、locator、claim mapping 和人工复核任务，确认该类别在当前论文材料中的真实语义。",
        }
    return definitions.get(pattern_key, definitions["other"])


def build_evidence_clusters(
    findings: list[dict[str, Any]],
    claim_mappings: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    manual_tasks: list[dict[str, Any]],
    source_reviews: dict[str, dict[str, Any]],
    judge_risks: list[dict[str, Any]],
    max_clusters: int = 6,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        source = str(finding.get("workbook") or finding.get("source_path") or finding.get("source_artifact") or "-")
        anchor = str(finding.get("sheet") or finding.get("figure") or finding.get("panel_id") or finding.get("category") or "-")
        grouped[(source, anchor)].append(finding)

    ranked_groups = sorted(
        grouped.items(),
        key=lambda item: (
            -max(risk_score(finding.get("risk_level")) for finding in item[1]),
            -len(item[1]),
            item[0][1],
            item[0][0],
        ),
    )

    clusters = []
    for index, ((source, anchor), group_findings) in enumerate(ranked_groups[:max_clusters], start=1):
        group_findings = sorted(
            group_findings,
            key=lambda finding: (
                -risk_score(finding.get("risk_level")),
                -finding_support_value(finding),
                str(finding.get("finding_id", "")),
            ),
        )
        finding_ids = [str(finding.get("finding_id")) for finding in group_findings if finding.get("finding_id")]
        matched_claims = claims_for_finding_ids(finding_ids, claims, claim_mappings)
        matched_tasks = tasks_for_finding_ids(finding_ids, manual_tasks)
        matched_risks = [risk for risk in judge_risks if any(ref_mentions_finding(ref, finding_ids) for ref in (risk.get("evidence_refs") or []))]
        reviews = [source_reviews[finding_id] for finding_id in finding_ids if finding_id in source_reviews]
        categories = Counter(str(finding.get("category", "-")) for finding in group_findings)
        risk = max((str(finding.get("risk_level", "medium")) for finding in group_findings), key=risk_score, default="medium")
        clusters.append(
            {
                "cluster_id": f"EC-{index:03d}",
                "workbook": source,
                "sheet": anchor,
                "risk_level": risk,
                "finding_ids": finding_ids,
                "findings": group_findings,
                "categories": categories,
                "claims": matched_claims,
                "manual_tasks": matched_tasks,
                "risks": matched_risks,
                "reviews": reviews,
                "headline": cluster_headline(anchor, group_findings, matched_claims),
                "signals": [finding_signal(finding) for finding in group_findings[:4]],
                "benign_explanations": cluster_benign_explanations(group_findings, reviews),
                "source_artifact": source_artifact_for_findings(group_findings),
            }
        )
    return clusters


def evidence_cluster_cards(clusters: list[dict[str, Any]]) -> str:
    if not clusters:
        return "<p class='muted'>未形成主证据簇。请查看技术附录中的原始工具输出。</p>"
    cards = []
    for index, cluster in enumerate(clusters, start=1):
        claims = cluster.get("claims") or []
        claim_items = [
            f"<li><code>{h(claim.get('claim_id', '-'))}</code> {h((claim.get('claim_text') or claim.get('text') or '-')[:260])}</li>"
            for claim in claims[:4]
        ] or ["<li class='muted'>未自动关联到具体 claim，需人工补映射。</li>"]
        tasks = cluster.get("manual_tasks") or []
        task_items = [str(task.get("question", "")) for task in tasks[:3] if task.get("question")]
        if not task_items:
            task_items = [
                "核对 Source Data 的 workbook/sheet/column header、row offset、merged cells 和 figure panel 语义。",
                "要求作者提供原始分析脚本或数据导出过程，解释该结构性模式是否来自合法归一化或批量派生。",
            ]
        categories = cluster.get("categories") or Counter()
        category_text = ", ".join(f"{category_label(key)}×{value}" for key, value in categories.most_common())
        cards.append(
            f"""
<article class="cluster-card" id="{h(cluster.get('cluster_id'))}">
  <div class="cluster-top">
    <div>
      <div class="cluster-title">
        <span class="rank">{h(index)}</span>
        <span class="badge {h(cluster.get('risk_level'))}">{h(risk_label(cluster.get('risk_level')))}</span>
        <h3>{h(cluster.get('sheet'))} · {h(category_text or '技术异常候选')}</h3>
      </div>
      <p><strong>为什么先看：</strong>{h(cluster.get('headline'))}</p>
      <ul class="signal-list">
        {list_items(cluster.get("signals") or [])}
      </ul>
    </div>
    <aside class="lane">
      <h3>证据定位</h3>
      <div class="kv">
        <div>cluster</div><div><code>{h(cluster.get('cluster_id'))}</code></div>
        <div>source</div><div><code>{h(cluster.get('workbook'))}</code></div>
        <div>anchor</div><div><code>{h(cluster.get('sheet'))}</code></div>
        <div>finding ids</div><div><code>{h(', '.join(cluster.get('finding_ids') or []))}</code></div>
        <div>artifact</div><div><code>{h(cluster.get('source_artifact'))}</code></div>
      </div>
    </aside>
  </div>
  <div class="grid cols-2 section">
    <div>
      <h3>影响的 claim</h3>
      <ul>{''.join(claim_items)}</ul>
    </div>
    <div>
      <h3>人工复核动作</h3>
      <ul>{list_items(task_items)}</ul>
    </div>
  </div>
  <details>
    <summary>可能的良性解释与原始 finding</summary>
    <div class="grid cols-2 section">
      <div><h3>良性解释</h3><ul>{list_items(cluster.get("benign_explanations") or [])}</ul></div>
      <div><h3>原始 finding</h3><ul>{list_items(cluster.get("finding_ids") or [])}</ul></div>
    </div>
  </details>
</article>
"""
        )
    return "\n".join(cards)


def brief_list(clusters: list[dict[str, Any]]) -> str:
    if not clusters:
        return "<p class='muted'>未生成主证据簇。建议先查看覆盖范围和技术附录。</p>"
    rows = []
    for index, cluster in enumerate(clusters[:4], start=1):
        rows.append(
            f"<li><span class='rank'>{h(index)}</span><span><strong>{h(cluster.get('sheet'))}</strong><br/><span class='muted'>{h(cluster.get('headline'))}</span></span></li>"
        )
    return "<ul class='brief-list'>" + "".join(rows) + "</ul>"


def claim_impact_matrix(
    source_mappings: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    canonical_mappings: list[dict[str, Any]],
) -> str:
    claims_by_id = {str(claim.get("claim_id")): claim for claim in claims if isinstance(claim, dict) and claim.get("claim_id")}
    rows = []
    if source_mappings:
        for mapping in source_mappings[:14]:
            if not isinstance(mapping, dict):
                continue
            claim = claims_by_id.get(str(mapping.get("claim_id"))) or {}
            refs = [str(ref) for ref in (mapping.get("source_data_refs") or mapping.get("evidence_refs") or [])]
            finding_refs = [ref for ref in refs if "forensics:" in ref or "finding" in ref.lower()]
            needs_review = mapping.get("needs_human_review")
            rows.append(
                "<tr>"
                f"<td><code>{h(mapping.get('claim_id', '-'))}</code></td>"
                f"<td>{h((claim.get('claim_text') or claim.get('text') or '-')[:260])}</td>"
                f"<td><code>{h(', '.join(refs[:4]) or '-')}</code></td>"
                f"<td><code>{h(', '.join(finding_refs[:6]) or '-')}</code></td>"
                f"<td><span class='badge {'warning' if needs_review is not False else 'low'}'>{h('需人工复核' if needs_review is not False else '低优先级')}</span></td>"
                "</tr>"
            )
    elif canonical_mappings:
        for mapping in canonical_mappings[:14]:
            if not isinstance(mapping, dict):
                continue
            claim = claims_by_id.get(str(mapping.get("claim_id"))) or {}
            refs = [str(ref) for ref in (mapping.get("evidence_refs") or [])]
            finding_refs = [str(ref) for ref in (mapping.get("finding_refs") or refs)]
            metadata = mapping.get("metadata") if isinstance(mapping.get("metadata"), dict) else {}
            needs_review = metadata.get("needs_human_review")
            rows.append(
                "<tr>"
                f"<td><code>{h(mapping.get('claim_id', '-'))}</code></td>"
                f"<td>{h((claim.get('claim_text') or claim.get('text') or '-')[:260])}</td>"
                f"<td><code>{h(', '.join(refs[:4]) or '-')}</code></td>"
                f"<td><code>{h(', '.join(finding_refs[:6]) or '-')}</code></td>"
                f"<td><span class='badge {'warning' if needs_review is not False else 'low'}'>{h('需人工复核' if needs_review is not False else '低优先级')}</span></td>"
                "</tr>"
            )
    if not rows:
        return "<p class='muted'>未生成 claim impact matrix。</p>"
    return (
        "<table><thead><tr><th>claim</th><th>claim 文本</th><th>Evidence refs</th><th>Finding refs</th><th>状态</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def claims_for_finding_ids(
    finding_ids: list[str],
    claims: list[dict[str, Any]],
    claim_mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    claim_ids = set()
    result: list[dict[str, Any]] = []
    for mapping in claim_mappings:
        if not isinstance(mapping, dict):
            continue
        refs = mapping.get("source_data_refs") or mapping.get("evidence_refs") or []
        if any(ref_mentions_finding(ref, finding_ids) for ref in refs):
            claim_id = mapping.get("claim_id")
            if claim_id:
                claim_ids.add(str(claim_id))
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        refs = claim.get("evidence_refs") or []
        claim_id = str(claim.get("claim_id", ""))
        if claim_id in claim_ids or any(ref_mentions_finding(ref, finding_ids) for ref in refs):
            result.append(claim)
    return result


def tasks_for_finding_ids(finding_ids: list[str], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        refs = task.get("evidence_refs") or []
        if any(ref_mentions_finding(ref, finding_ids) for ref in refs):
            result.append(task)
    return result


def ref_mentions_finding(ref: Any, finding_ids: list[str]) -> bool:
    text = json.dumps(ref, ensure_ascii=False) if isinstance(ref, dict) else str(ref)
    return any(finding_id and finding_id in text for finding_id in finding_ids)


def cluster_headline(sheet: str, findings: list[dict[str, Any]], claims: list[dict[str, Any]]) -> str:
    categories = Counter(str(finding.get("category", "-")) for finding in findings)
    category_text = "、".join(category_label(category) for category, _ in categories.most_common(3))
    claim_hint = ""
    if claims:
        claim_hint = f"；已关联 {len(claims)} 条 claim"
    return f"{sheet} 聚集了 {len(findings)} 条高优先级信号，主要模式为 {category_text or '技术异常候选'}{claim_hint}，建议作为人工复核入口。"


def finding_signal(finding: dict[str, Any]) -> str:
    category = str(finding.get("category", "-"))
    support = support_text(finding)
    columns = finding.get("columns") or finding.get("column_pair") or finding.get("column") or []
    columns_text = ", ".join(str(item) for item in columns) if isinstance(columns, list) else str(columns)
    if category == "row_offset_scalar_multiple":
        return (
            f"固定行偏移 {finding.get('row_offset', '-')} 后出现标量关系；"
            f"列 {columns_text or '-'}；{support}。"
        )
    if category == "long_format_paired_ratio_reuse":
        return (
            f"long-format paired 数据在 pair_id 偏移 {finding.get('pair_id_offset', '-')} 后出现比例复用；"
            f"列 {columns_text or '-'}；{support}。"
        )
    if category == "duplicate_row_vector":
        return (
            f"低宽度行向量重复；重复行数 {finding.get('duplicate_row_count', '-')}"
            f"；列 {columns_text or '-'}。"
        )
    if category == "long_format_within_pair_ratio_enrichment":
        return (
            f"paired 数据内部特定比例富集；matched_pair_groups={finding.get('matched_pair_groups', '-')}"
            f"；列 {columns_text or '-'}。"
        )
    if category == "row_offset_partial_copy_rounding_bias":
        return (
            f"行偏移后出现部分复制与四舍五入偏差；exact_reuse_pairs={finding.get('exact_reuse_pairs', '-')}"
            f"；{support}。"
        )
    if columns_text:
        return f"{category_label(category)}；{support}；列 {columns_text}。"
    return f"{category_label(category)}；{support}；source={evidence_source_text(finding)}。"


def cluster_benign_explanations(findings: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for review in reviews:
        items.extend(str(item) for item in (review.get("benign_explanations") or [])[:3])
    for finding in findings:
        items.extend(str(item) for item in (finding.get("benign_explanations") or [])[:2])
    if not items:
        pattern_keys = {pattern_key_for_finding(finding) for finding in findings}
        if "visual_forensics" in pattern_keys:
            items = [
                "该模式可能来自同一主体多通道成像、合法 control 复用、裁剪导出或压缩伪影。",
                "需要结合原图、panel/caption、相似方法、分数和实验条件判断。",
            ]
        elif "execution_evidence" in pattern_keys:
            items = [
                "该模式可能来自环境差异、随机种子、依赖版本或输入材料不完整。",
                "需要结合 runtime manifest、命令日志、结果文件 hash 和 claim 映射判断。",
            ]
        else:
            items = [
                "该模式可能来自合法的归一化、批量派生、配对样本排序或模板化导出。",
                "需要结合原始 artifact、导出参数、字段定义和论文 claim 语义判断。",
            ]
    return dedupe(items)[:5]


def source_artifact_for_findings(findings: list[dict[str, Any]]) -> str:
    artifacts = dedupe([source_artifact_for_finding(finding) for finding in findings])
    return ", ".join(artifacts[:3]) or "-"


def source_artifact_for_finding(finding: dict[str, Any]) -> str:
    if finding.get("source_artifact"):
        return str(finding.get("source_artifact"))
    pair_categories = {
        "row_offset_scalar_multiple",
        "long_format_paired_ratio_reuse",
        "duplicate_row_vector",
        "long_format_within_pair_ratio_enrichment",
        "row_offset_partial_copy_rounding_bias",
    }
    if str(finding.get("category")) in pair_categories:
        return SOURCE_DATA_PAIR_FORENSICS_ARTIFACT
    if finding.get("workbook") or finding.get("sheet"):
        return SOURCE_DATA_FINDINGS_ARTIFACT
    return "static_audit_bundle.json"


def finding_support_value(finding: dict[str, Any]) -> int:
    for key in ("support_rows", "matched_pairs", "matched_pair_groups", "duplicate_row_count", "exact_reuse_pairs", "equal_rows"):
        value = finding.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return 0


def finding_card(
    finding: dict[str, Any],
    mappings: list[dict[str, Any]],
    source_review: dict[str, Any],
    risk: dict[str, Any] | None,
) -> str:
    finding_id = str(finding.get("finding_id", "-"))
    category = str(finding.get("category", "-"))
    risk_level = str(finding.get("risk_level", "medium"))
    relation = relation_text(finding)
    support = support_text(finding)
    refs = paper_refs(mappings)
    first_ref = best_paper_ref(refs)
    locator = source_locator(finding, first_ref)
    benign = source_review.get("benign_explanations") or finding.get("benign_explanations") or []
    review_action = review_question(source_review, risk, finding)
    sample_rows = sample_evidence_html(finding)
    claim_text = first_claim(mappings)
    risk_reason = (risk or {}).get("reason", "")
    source_artifact = source_artifact_for_finding(finding)
    mapping_note = mapping_granularity_note(finding)
    return f"""
<article class="finding-card">
  <div>
    <div class="finding-title">
      <span class="badge high">人工复核候选</span>
      <span class="badge {h(risk_level)}">{h(risk_label(risk_level))}</span>
      <h3>{h(finding_id)} · {h(category_label(category))}</h3>
    </div>
    <p><strong>风险摘要：</strong>{h(risk_reason or default_finding_summary(finding))}</p>
    <div class="quote"><strong>关联 claim：</strong>{h(claim_text or "未自动抽取到 claim 文本，需人工补映射。")}</div>
    <div class="grid cols-2">
      <div>
        <h3>为什么值得复核</h3>
        <ul>
          <li>{h(relation)}</li>
          <li>{h(support)}</li>
          <li>{h(mapping_note)}</li>
        </ul>
      </div>
      <div>
        <h3>良性解释</h3>
        <ul>{list_items(benign[:4])}</ul>
      </div>
    </div>
    <h3>人工复核动作</h3>
    <p>{h(review_action)}</p>
    <details>
      <summary>样本行</summary>
      {sample_rows}
    </details>
  </div>
  <aside class="lane">
    <h3>证据定位</h3>
      <div class="kv">
        <div>finding_id</div><div><code>{h(finding_id)}</code></div>
        <div>source</div><div><code>{h(evidence_source_text(finding))}</code></div>
        <div>locator</div><div><code>{h(evidence_locator(finding))}</code></div>
        <div>support</div><div>{h(support)}</div>
        <div>论文 figure</div><div>{h(locator["figure"])}</div>
        <div>full.md 行号</div><div>{h(locator["line"])}</div>
        <div>PDF 定位</div><div>{pdf_locator_html(first_ref)}</div>
        <div>artifact</div><div><code>{h(source_artifact)}</code></div>
      </div>
    </aside>
</article>
"""


def evidence_card_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        findings,
        key=lambda item: (
            -risk_score(item.get("risk_level")),
            -int(item.get("support_rows") or item.get("equal_rows") or 0),
            str(item.get("finding_id", "")),
        ),
    )[:MAX_EVIDENCE_CARDS]


def steps_table(steps: list[dict[str, Any]]) -> str:
    rows = []
    for step in steps:
        key = step.get("key") or step.get("step_key") or "-"
        title = step.get("title", key)
        status = step.get("status", "-")
        detail = str(step.get("detail", ""))[:120]
        rows.append(
            f"<tr><td><code>{h(key)}</code></td><td>{h(title)}</td><td><span class='badge {h(status)}'>{h(status_label(status))}</span></td><td>{h(detail)}</td></tr>"
        )
    return "<table><thead><tr><th>步骤 key</th><th>步骤</th><th>状态</th><th>说明</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def traces_table(traces: list[dict[str, Any]]) -> str:
    rows = []
    counts = Counter(trace.get("status", "-") for trace in traces)
    for trace in traces:
        status = str(trace.get("status", "-"))
        rows.append(
            f"<tr><td><code>{h(trace.get('role_id', '-'))}</code></td><td><span class='badge {h(status)}'>{h(status_label(status))}</span></td><td>{h(summary_text(trace.get('output_summary') or {}))}</td><td><code>{h(trace.get('output_path', '-'))}</code></td></tr>"
        )
    summary = " ".join(f"<span class='badge {h(k)}'>{h(status_label(k))} {v}</span>" for k, v in sorted(counts.items()))
    return f"<p>{summary}</p><table><thead><tr><th>role</th><th>状态</th><th>摘要</th><th>输出文件</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def material_plan_panel(material_summary: dict[str, Any], material_plan: dict[str, Any]) -> str:
    lanes = [lane for lane in (material_plan.get("selected_optional_lanes") or []) if isinstance(lane, dict)]
    lane_rows = []
    for lane in lanes:
        status = str(lane.get("status", "-"))
        lane_rows.append(
            "<tr>"
            f"<td><code>{h(lane.get('lane_id', '-'))}</code></td>"
            f"<td><span class='badge {h(status)}'>{h(status_label(status))}</span></td>"
            f"<td><code>{h(lane.get('root') or '-')}</code></td>"
            f"<td>{h(lane.get('reason', '-'))}</td>"
            "</tr>"
        )
    if not lane_rows:
        lane_rows.append("<tr><td colspan='4'>未选择 optional lane。</td></tr>")
    material_types = material_summary.get("by_material_type") if isinstance(material_summary.get("by_material_type"), dict) else {}
    unsupported = material_plan.get("unsupported_materials") or []
    unsupported_text = ", ".join(
        str(item.get("path", item)) for item in unsupported[:6] if isinstance(item, dict)
    ) or "-"
    return f"""
<div class="grid cols-2">
  <div class="lane">
    <h3>材料清单</h3>
    <div class="kv">
      <div>文件数</div><div>{h(material_summary.get("file_count", "-"))}</div>
      <div>材料类型</div><div>{h(", ".join(f"{key}={value}" for key, value in material_types.items()) or "-")}</div>
      <div>候选根目录</div><div>{h(material_summary.get("candidate_source_roots", "-"))}</div>
      <div>可执行 lane</div><div>{h(material_summary.get("supported_optional_lanes", "-"))}</div>
    </div>
  </div>
  <div class="lane">
    <h3>Agent Material Plan</h3>
    <div class="kv">
      <div>状态</div><div>{h(status_label(material_plan.get("status", "ok") if material_plan else "missing"))}</div>
      <div>缺失材料</div><div>{h(", ".join(str(item) for item in (material_plan.get("missing_materials") or [])) or "-")}</div>
      <div>暂不支持材料</div><div>{h(unsupported_text)}</div>
    </div>
  </div>
</div>
<table>
  <thead><tr><th>lane</th><th>状态</th><th>根目录</th><th>选择原因</th></tr></thead>
  <tbody>{''.join(lane_rows)}</tbody>
</table>
"""


def canonical_mapping_table(claims: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> str:
    mappings = [mapping for mapping in mappings if isinstance(mapping, dict)]
    claims = [claim for claim in claims if isinstance(claim, dict)]
    if not mappings:
        return "<p class='muted'>未生成 Agent refined mapping；如存在 deterministic scaffolding，请查看对应工具 JSON。</p>"
    claim_by_id = {str(claim.get("claim_id")): claim for claim in claims if claim.get("claim_id")}
    rows = []
    for mapping in mappings[:12]:
        claim = claim_by_id.get(str(mapping.get("claim_id"))) or {}
        metadata = mapping.get("metadata") if isinstance(mapping.get("metadata"), dict) else {}
        source_refs = metadata.get("source_data_refs") or mapping.get("evidence_refs") or []
        needs_review = metadata.get("needs_human_review")
        rows.append(
            "<tr>"
            f"<td><code>{h(mapping.get('mapping_id', '-'))}</code></td>"
            f"<td><code>{h(mapping.get('claim_id', '-'))}</code></td>"
            f"<td>{h(str(claim.get('text', '-'))[:260])}</td>"
            f"<td>{h(mapping.get('confidence', '-'))}</td>"
            f"<td><span class='badge warning'>{h('需人工复核' if needs_review is not False else '低优先级')}</span></td>"
            f"<td><code>{h(', '.join(str(ref) for ref in source_refs[:4]) or '-')}</code></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>mapping</th><th>claim</th><th>claim 文本</th><th>置信度</th><th>复核状态</th><th>Evidence refs</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def manual_tasks_table(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "<p class='muted'>Agent 未生成独立人工复核任务。</p>"
    rows = []
    for task in tasks[:10]:
        refs = task.get("evidence_refs") or []
        priority = str(task.get("priority", "-"))
        rows.append(
            "<tr>"
            f"<td><code>{h(task.get('task_id', '-'))}</code></td>"
            f"<td><span class='badge {h(priority)}'>{h(risk_label(priority))}</span></td>"
            f"<td>{h(task.get('question', '-'))}</td>"
            f"<td><code>{h(', '.join(str(ref) for ref in refs[:5]) or '-')}</code></td>"
            "</tr>"
        )
    return "<table><thead><tr><th>task</th><th>优先级</th><th>问题</th><th>证据 refs</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def pair_forensics_table(findings: list[dict[str, Any]]) -> str:
    findings = [finding for finding in findings if isinstance(finding, dict)]
    if not findings:
        return "<p class='muted'>未生成 pair/row-offset priority finding。</p>"
    rows = []
    for finding in findings[:12]:
        risk = str(finding.get("risk_level", "medium"))
        support = (
            finding.get("support_rows")
            or finding.get("matched_pairs")
            or finding.get("matched_pair_groups")
            or finding.get("duplicate_row_count")
            or finding.get("exact_reuse_pairs")
            or "-"
        )
        overlap = finding.get("overlap_rows") or finding.get("overlap_pairs") or finding.get("overlap_pair_groups") or "-"
        columns = finding.get("columns") or finding.get("column_pair") or finding.get("column") or []
        if isinstance(columns, list):
            columns_text = ", ".join(str(item) for item in columns)
        else:
            columns_text = str(columns)
        rows.append(
            "<tr>"
            f"<td><code>{h(finding.get('finding_id', '-'))}</code></td>"
            f"<td><span class='badge {h(risk)}'>{h(risk_label(risk))}</span></td>"
            f"<td>{h(finding.get('category', '-'))}</td>"
            f"<td><code>{h(finding.get('workbook', '-'))}</code></td>"
            f"<td>{h(finding.get('sheet', '-'))}</td>"
            f"<td>{h(finding.get('row_offset') or finding.get('pair_id_offset') or '-')}</td>"
            f"<td>{h(columns_text or '-')}</td>"
            f"<td>{h(support)}/{h(overlap)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>ID</th><th>风险</th><th>类别</th><th>workbook</th><th>sheet</th><th>offset</th><th>columns</th><th>support</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def investigation_table(records: list[dict[str, Any]]) -> str:
    records = [record for record in records if isinstance(record, dict)]
    if not records:
        return "<p class='muted'>本次未生成 investigation round 记录。</p>"
    rows = []
    for record in records[:20]:
        status = str(record.get("status", "skipped"))
        artifacts = record.get("output_artifacts") or []
        rows.append(
            "<tr>"
            f"<td>{h(record.get('round_id', '-'))}</td>"
            f"<td><code>{h(record.get('action_id', '-'))}</code></td>"
            f"<td><code>{h(record.get('tool_id', '-'))}</code></td>"
            f"<td><span class='badge {h(status)}'>{h(risk_label(status))}</span></td>"
            f"<td>{h(str(record.get('hypothesis') or record.get('detail') or '-')[:220])}</td>"
            f"<td>{h(', '.join(str(item) for item in artifacts[:3]) or '-')}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Round</th><th>Action</th><th>Tool</th><th>Status</th><th>Hypothesis / Detail</th><th>Artifact</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def risks_table(risks: list[dict[str, Any]]) -> str:
    rows = []
    for risk in risks:
        rows.append(
            f"<tr><td><span class='badge {h(risk.get('risk_level', '-'))}'>{h(risk_label(risk.get('risk_level', '-')))}</span></td><td>{h(risk.get('reason', ''))}</td><td>{h(', '.join(str(item) for item in (risk.get('evidence_refs') or [])[:8]))}</td></tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='3'>JudgeAgent 未生成风险建议。</td></tr>")
    return "<table><thead><tr><th>风险</th><th>原因</th><th>证据 refs</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def artifact_links(workdir: Path) -> str:
    names = [
        "final_audit_report.md",
        "static_audit_bundle.json",
        "audit_run_manifest.json",
        "material_inventory.json",
        "agent_material_plan.json",
        "source_data_findings.json",
        "source_data_pair_forensics.json",
        "agent_claim_extractor.json",
        "agent_source_data_auditor.json",
        "agent_judge.json",
        "evidence_ledger.json",
        "numeric_forensics.json",
        "exact_image_duplicates.json",
        "image_similarity_candidates.json",
        "investigation_rounds.jsonl",
    ]
    rows = []
    for name in names:
        path = workdir / name
        status = "present" if path.exists() else "missing"
        size = path.stat().st_size if path.exists() else "-"
        rows.append(
            f"<div class='artifact'><span><code>{h(name)}</code></span><span><span class='badge {status}'>{status_label(status)}</span> {h(size)} 字节</span></div>"
        )
    return "<div class='artifact-list'>" + "".join(rows) + "</div>"


def collect_limitations(bundle: dict[str, Any], agent_judge: dict[str, Any], similarity: dict[str, Any]) -> list[str]:
    limitations = ["本报告不做最终科研诚信判定，只生成技术事实候选和人工复核入口。"]
    if bundle.get("claim_mappings"):
        limitations.append("claim-to-evidence mapping 仍是候选映射，需要按原始 artifact 和 locator 人工确认。")
    else:
        limitations.append("本次未生成 canonical claim-to-evidence mapping，claim 影响需要人工补齐。")
    execution_status = (bundle.get("execution_status") or {}).get("status")
    if execution_status in {None, "", "not_provided", "not_run", "missing_material"}:
        limitations.append(f"代码执行审查未形成可用执行证据，execution_status={execution_status or 'unknown'}。")
    if similarity.get("status") == "not_available":
        limitations.append("近似图像相似度未运行；只能说明 exact duplicate 未发现。")
    limitations.extend(str(item) for item in (bundle.get("limitations") or [])[:5])
    limitations.extend(str(item) for item in (agent_judge.get("limitations") or [])[:5])
    return dedupe(limitations)


def dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        key = str(finding.get("finding_id") or json.dumps(finding, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def map_findings_to_mappings(mappings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for mapping in mappings:
        for finding in mapping.get("linked_priority_findings") or []:
            finding_id = finding.get("finding_id") if isinstance(finding, dict) else None
            if finding_id:
                result.setdefault(str(finding_id), []).append(mapping)
    return result


def map_reviews(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("finding_id")): item for item in reviews if item.get("finding_id")}


def risk_for_finding(risks: list[dict[str, Any]], finding_id: Any) -> dict[str, Any] | None:
    if finding_id is None:
        return None
    for risk in risks:
        if str(finding_id) in {str(item) for item in (risk.get("evidence_refs") or [])}:
            return risk
    return None


def paper_refs(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for mapping in mappings:
        refs.extend(ref for ref in (mapping.get("matched_paper_references") or []) if isinstance(ref, dict))
    return refs


def best_paper_ref(refs: list[dict[str, Any]]) -> dict[str, Any]:
    for ref in refs:
        text = str(ref.get("text", ""))
        if "See next page" not in text and len(text) > 40:
            return ref
    return refs[0] if refs else {}


def source_locator(finding: dict[str, Any], paper_ref: dict[str, Any]) -> dict[str, str]:
    line_start = paper_ref.get("line_start")
    line_end = paper_ref.get("line_end")
    if line_start and line_end and line_start != line_end:
        line = f"full.md:{line_start}-{line_end}"
    elif line_start:
        line = f"full.md:{line_start}"
    else:
        line = "未定位"
    return {
        "figure": str(paper_ref.get("match_label") or "-"),
        "line": line,
    }


def first_claim(mappings: list[dict[str, Any]]) -> str:
    for mapping in mappings:
        claims = mapping.get("candidate_claims") or []
        if claims and isinstance(claims[0], dict):
            return str(claims[0].get("text", ""))[:700]
        refs = mapping.get("matched_paper_references") or []
        if refs and isinstance(refs[0], dict):
            return str(refs[0].get("text", ""))[:700]
    return ""


def relation_text(finding: dict[str, Any]) -> str:
    if finding.get("category") == "fixed_difference":
        return f"固定差关系：{finding.get('relationship_value')}，列 {', '.join(finding.get('column_pair') or [])}。"
    if finding.get("category") == "duplicate_numeric_columns":
        return f"数值列完全重复：列 {', '.join(finding.get('column_pair') or [])}。"
    if finding.get("category") == "row_offset_scalar_multiple":
        return f"固定行偏移 {finding.get('row_offset', '-')} 后存在标量关系，列 {', '.join(str(item) for item in (finding.get('columns') or [])) or '-'}。"
    if finding.get("category") == "long_format_paired_ratio_reuse":
        return f"pair_id 偏移 {finding.get('pair_id_offset', '-')} 后出现配对两组比例复用，列 {', '.join(str(item) for item in (finding.get('columns') or [])) or '-'}。"
    if finding.get("category") == "duplicate_row_vector":
        return f"低宽度行向量重复，重复行数 {finding.get('duplicate_row_count', '-')}。"
    return str(finding.get("category", "-"))


def support_text(finding: dict[str, Any]) -> str:
    support = (
        finding.get("support_rows")
        or finding.get("matched_pairs")
        or finding.get("matched_pair_groups")
        or finding.get("duplicate_row_count")
        or finding.get("exact_reuse_pairs")
        or finding.get("equal_rows")
    )
    overlap = finding.get("overlap_rows") or finding.get("overlap_pairs") or finding.get("overlap_pair_groups")
    if support and overlap:
        return f"支持行数 {support}/{overlap}，support_rate={finding.get('support_rate', '-')}"
    if support:
        return f"支持行数 {support}"
    return "support 未记录"


def default_finding_summary(finding: dict[str, Any]) -> str:
    columns = finding.get("column_pair") or finding.get("columns") or finding.get("column") or []
    columns_text = ", ".join(str(item) for item in columns) if isinstance(columns, list) else str(columns)
    if finding.get("workbook") or finding.get("sheet"):
        return (
            f"{finding.get('workbook', '-')} / {finding.get('sheet', '-')} 中 "
            f"{columns_text or evidence_locator(finding)} 出现 {finding.get('category', '-')}。"
        )
    source = evidence_source_text(finding)
    locator = evidence_locator(finding)
    if source != "-" or locator != "-":
        return f"{source} / {locator} 出现 {finding.get('category', '-')}。"
    return str(finding.get("summary") or finding.get("category") or "技术事实候选。")


def review_question(source_review: dict[str, Any], risk: dict[str, Any] | None, finding: dict[str, Any]) -> str:
    if risk and risk.get("requires_human_review"):
        return str(risk.get("reason", "请人工复核该 finding 的证据定位、claim 影响和良性解释。"))
    refs = source_review.get("evidence_refs") if isinstance(source_review.get("evidence_refs"), dict) else {}
    linked = refs.get("linked_claims") if refs else None
    linked_text = f"关联 claim: {', '.join(linked)}。" if linked else ""
    if finding.get("workbook") or finding.get("sheet"):
        return f"请核对 workbook/sheet/column header、merged cells、figure panel 和原始实验语义。{linked_text}"
    return f"请核对原始 artifact、locator、claim 影响、工具输出参数和最强良性解释。{linked_text}"


def mapping_granularity_note(finding: dict[str, Any]) -> str:
    source_artifact = source_artifact_for_finding(finding)
    if source_artifact in {SOURCE_DATA_FINDINGS_ARTIFACT, SOURCE_DATA_PAIR_FORENSICS_ARTIFACT}:
        return "当前映射多为 figure/sheet 级，panel/column-block 级仍需人工确认。"
    return "当前映射需要回到原始 artifact、locator 和 claim 逐条确认。"


def pdf_locator_html(paper_ref: dict[str, Any]) -> str:
    page = paper_ref.get("page") or paper_ref.get("page_number")
    bbox = paper_ref.get("bbox")
    if page or bbox:
        parts = []
        if page:
            parts.append(f"page={page}")
        if bbox:
            parts.append(f"bbox={bbox}")
        return f"<code>{h('; '.join(parts))}</code>"
    return '<span class="badge skipped">page/bbox 未记录</span>'


def sample_evidence_html(finding: dict[str, Any]) -> str:
    pairs = finding.get("sample_pairs") or []
    if pairs:
        return sample_pairs_html(pairs)
    sample = evidence_sample_text(finding)
    if sample == "-":
        return "<p class='muted'>没有可展示的样本值、公式或摘要。</p>"
    return f"<p>{h(sample)}</p>"


def sample_pairs_html(samples: list[dict[str, Any]]) -> str:
    if not samples:
        return "<p class='muted'>没有可展示的样本行。</p>"
    rows = [
        f"<div class='sample-row'><div>行 {h(item.get('row', '-'))}</div><div>{h(item.get('left', '-'))}</div><div>{h(item.get('right', '-'))}</div></div>"
        for item in samples[:8]
    ]
    return "<div class='samples'><div class='sample-row muted'><div>行</div><div>左列</div><div>右列</div></div>" + "".join(rows) + "</div>"


def metric(label: str, value: Any) -> str:
    return f"<div class='metric'><div class='num'>{h(value)}</div><div class='label'>{h(label)}</div></div>"


def list_items(items: list[Any]) -> str:
    if not items:
        return "<li class='muted'>未记录。</li>"
    return "".join(f"<li>{h(item)}</li>" for item in items)


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def summary_text(summary: dict[str, Any]) -> str:
    parts = []
    for key, value in summary.items():
        if isinstance(value, (str, int, float)):
            parts.append(f"{key}={str(value)[:90]}")
    return "; ".join(parts[:4]) or "-"


def status_label(status: Any) -> str:
    labels = {
        "ran": "已执行",
        "reused": "已复用",
        "skipped": "已跳过",
        "warning": "警告",
        "failed": "失败",
        "not_run": "未运行",
        "not_provided": "未提供",
        "missing_material": "材料缺失",
        "selected": "已选择",
        "unsupported": "暂不支持",
        "present": "已生成",
        "missing": "缺失",
        "ok": "正常",
        "not_available": "不可用",
    }
    return labels.get(str(status), str(status))


def risk_label(risk: Any) -> str:
    labels = {
        "critical": "严重风险",
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
        "info": "提示",
    }
    return labels.get(str(risk), str(risk))


def risk_score(risk: Any) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
        "info": 0,
    }.get(str(risk), 0)


def category_label(category: Any) -> str:
    labels = {
        "duplicate_numeric_columns": "数值列重复",
        "fixed_difference": "固定差关系",
        "fixed_ratio": "固定比例关系",
        "formula_derived_columns": "公式派生列",
        "row_offset_scalar_multiple": "固定行偏移标量关系",
        "long_format_paired_ratio_reuse": "配对比例复用",
        "duplicate_row_vector": "行向量重复",
        "long_format_within_pair_ratio_enrichment": "配对内部比例富集",
        "row_offset_partial_copy_rounding_bias": "行偏移复制/舍入偏差",
    }
    return labels.get(str(category), str(category))


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def h(value: Any) -> str:
    if value is None:
        return ""
    return escape(str(value), quote=True)
