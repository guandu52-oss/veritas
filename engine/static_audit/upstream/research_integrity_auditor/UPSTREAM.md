# research-integrity-auditor upstream snapshot

本目录是 Veritas 对 `third_party/research-integrity-auditor` 的只读能力镜像。

## Source

- upstream url: `https://github.com/cylqwe7855-alt/research-integrity-auditor`
- upstream commit: `072bcf010fdcdd4a87a2888777c3002fdd22d31d`
- copied at: `2026-05-26`

## Contents

- `references/`: upstream audit methodology and integration references.
- `scripts/`: upstream deterministic tools for MinerU parsing, evidence ledger, numeric forensics, and evidence table rendering.
- `README.md`: upstream usage notes.

## Rules

- Do not edit files in this snapshot to express Veritas product behavior.
- Product behavior belongs in `engine/static_audit/`, `configs/methodology/`, and adapters.
- If upstream changes are needed, update this snapshot in one explicit sync commit and update this file.
- If Veritas needs patched behavior, implement it in first-party tools or adapters rather than mutating this snapshot.

