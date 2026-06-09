from __future__ import annotations

import json
import zipfile
from pathlib import Path

from engine.static_audit.tools.source_data_pair_forensics import (
    PairForensicsParams,
    _build_suffix_index,
    analyze_xlsx_root,
)


def write_minimal_xlsx(path: Path, rows: list[list[float | int | None]]) -> None:
    sheet_rows = []
    for row_index, values in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(values, start=1):
            if value is None:
                continue
            col = chr(64 + col_index)
            cells.append(f'<c r="{col}{row_index}"><v>{value}</v></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Fig.1a" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def test_pair_forensics_detects_row_offset_ratio_and_scalar_patterns(tmp_path) -> None:
    # Rows 1-4 and 5-8 are not paper-specific. They model the generic pattern:
    # a second block reuses the first block's paired ratios and scalar-multiplies
    # the underlying columns at a fixed row offset.
    write_minimal_xlsx(
        tmp_path / "source.xlsx",
        [
            [1, 2],
            [2, 6],
            [3, 12],
            [4, 20],
            [10, 20],
            [20, 60],
            [30, 120],
            [40, 200],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [7, 8],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [None, None],
            [7, 8],
        ],
    )

    result = analyze_xlsx_root(
        tmp_path,
        PairForensicsParams(min_pairs=4, min_support=1.0, max_offset=8),
    )
    categories = {item["category"] for item in result["findings"]}

    assert "paired_ratio_reuse" in categories
    assert "row_offset_scalar_multiple" in categories
    assert "duplicate_row_vector" in categories
    assert result["summary"]["priority_findings"] >= 3


def test_pair_forensics_detects_long_format_paired_ratio_reuse(tmp_path) -> None:
    # Long-format source data is common in biomedical papers: one column stores
    # pair/sample id and another stores the measurement for two consecutive rows.
    write_minimal_xlsx(
        tmp_path / "long_format.xlsx",
        [
            [None, 1, 1],
            [None, 1, 2],
            [None, 2, 2],
            [None, 2, 6],
            [None, 3, 3],
            [None, 3, 12],
            [None, 4, 4],
            [None, 4, 20],
            [None, 5, 10],
            [None, 5, 20],
            [None, 6, 20],
            [None, 6, 60],
            [None, 7, 30],
            [None, 7, 120],
            [None, 8, 40],
            [None, 8, 200],
        ],
    )

    result = analyze_xlsx_root(
        tmp_path,
        PairForensicsParams(min_pairs=4, min_support=1.0, max_offset=4),
    )
    categories = {item["category"] for item in result["findings"]}

    assert "long_format_paired_ratio_reuse" in categories


def test_pair_forensics_cli_outputs_empty_summary(tmp_path) -> None:
    output = tmp_path / "pair_forensics.json"
    result = analyze_xlsx_root(tmp_path, PairForensicsParams())
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["summary"]["workbook_count"] == 0
    assert data["summary"]["findings"] == 0


# ---------------------------------------------------------------------------
# New detectors (small-group / cross-sheet)
# ---------------------------------------------------------------------------


def write_minimal_two_sheet_xlsx(path: Path, sheet1_rows: list, sheet2_name: str, sheet2_rows: list) -> None:
    """Write an XLSX with two sheets for cross-sheet testing."""
    import zipfile as zf_mod

    def _sheet_xml(rows):
        sheet_rows = []
        for row_index, values in enumerate(rows, start=1):
            cells = []
            for col_index, value in enumerate(values, start=1):
                if value is None:
                    continue
                col = chr(64 + col_index)
                cells.append(f'<c r="{col}{row_index}"><v>{value}</v></c>')
            sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(sheet_rows)}</sheetData>'
            "</worksheet>"
        )

    with zf_mod.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>")
        zf.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>")
        zf.writestr("xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets>'
            '<sheet name="Fig.1a" sheetId="1" r:id="rId1"/>'
            f'<sheet name="{sheet2_name}" sheetId="2" r:id="rId2"/>'
            '</sheets>'
            "</workbook>")
        zf.writestr("xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            "</Relationships>")
        zf.writestr("xl/worksheets/sheet1.xml", _sheet_xml(sheet1_rows))
        zf.writestr("xl/worksheets/sheet2.xml", _sheet_xml(sheet2_rows))


def test_small_group_fixed_offset_detected(tmp_path) -> None:
    """Arithmetic progression: pairwise diffs repeat → same fixed offset detected."""
    # [1.0, 1.1, 1.2] → diffs: 0.1 (×2), 0.2 (×1) → 0.1 appears 2× ≥ min_small_group_size=2
    write_minimal_xlsx(
        tmp_path / "source.xlsx",
        [
            [None, 1.0],
            [None, 1.1],
            [None, 1.2],
        ],
    )
    result = analyze_xlsx_root(
        tmp_path,
        PairForensicsParams(min_small_group_size=2, max_small_group_size=7),
    )
    categories = {item["category"] for item in result["findings"]}
    assert "small_group_fixed_relationship" in categories
    sgr = [f for f in result["findings"] if f["category"] == "small_group_fixed_relationship"]
    assert any(f["risk_level"] in ("high", "medium") for f in sgr), f"Expected SGR finding, got: {sgr}"


def test_perfect_duplicate_values_detected(tmp_path) -> None:
    """3 biological replicates with identical value — CV=0%."""
    write_minimal_xlsx(
        tmp_path / "source.xlsx",
        [
            [None, 171.033379],
            [None, 171.033379],
            [None, 171.033379],
        ],
    )
    result = analyze_xlsx_root(tmp_path, PairForensicsParams())
    categories = {item["category"] for item in result["findings"]}
    assert "perfect_duplicate_values" in categories
    pdv = [f for f in result["findings"] if f["category"] == "perfect_duplicate_values"]
    assert any(f["risk_level"] in ("critical", "high") for f in pdv)


def test_cross_sheet_decimal_match_detected(tmp_path) -> None:
    """Two sheets where paired values share trailing digits with round diffs."""
    write_minimal_two_sheet_xlsx(
        tmp_path / "cross_sheet.xlsx",
        [
            [5.438083],
            [6.141738],
        ],
        "Fig. 7e",
        [
            [5.838083],
            [6.041738],
        ],
    )
    result = analyze_xlsx_root(
        tmp_path,
        PairForensicsParams(min_decimal_match_length=6, min_small_group_size=2, min_cross_sheet_matches=2, min_cross_sheet_fraction=0.5),
    )
    categories = {item["category"] for item in result["findings"]}
    assert "cross_sheet_decimal_match" in categories, f"Expected CSD, got categories: {categories}"


def test_build_suffix_index_groups_by_trailing_digits() -> None:
    """Values sharing the last N fractional digits land in the same bucket.

    _build_suffix_index quantizes to 12 DP, so values with fewer decimal
    places get zero-padded.  Use 12-DP values to test clean suffix matches.
    """
    from decimal import Decimal

    vals = [
        Decimal("5.438083123456"),
        Decimal("5.838083123456"),  # same suffix '123456'
        Decimal("6.141738654321"),
        Decimal("6.041738654321"),  # same suffix '654321'
        Decimal("0.123456789012"),  # unique suffix
    ]
    index = _build_suffix_index(vals, min_match_len=6)
    # 5.438083123456 and 5.838083123456 share last 6 digits '123456'
    assert len(index["123456"]) == 2
    # 6.141738654321 and 6.041738654321 share last 6 digits '654321'
    assert len(index["654321"]) == 2
    # 0.123456789012 has suffix '789012' → alone
    assert len(index["789012"]) == 1


def test_hub_sheets_summary_aggregates_cross_sheet_findings(tmp_path) -> None:
    """Two workbooks with cross-sheet decimal matches → one hub detected."""
    # Workbook A: "Source Data.xlsx" / sheet "Fig.1a"
    write_minimal_xlsx(
        tmp_path / "Source Data.xlsx",
        [
            [5.438083123456, 6.141738654321],
            [10.0, 20.0],
        ],
    )
    # Workbook B: same trailing digits, round diff of 0.4
    write_minimal_xlsx(
        tmp_path / "Supplementary.xlsx",
        [
            [5.838083123456, 6.041738654321],
            [11.0, 21.0],
        ],
    )
    result = analyze_xlsx_root(
        tmp_path,
        PairForensicsParams(
            min_decimal_match_length=6,
            min_small_group_size=2,
            min_cross_sheet_matches=2,
            min_cross_sheet_fraction=0.3,
        ),
    )
    hub_summaries = result.get("hub_sheets_summary", [])
    assert len(hub_summaries) >= 1, f"Expected at least 1 hub, got {hub_summaries}"
    hub = hub_summaries[0]
    assert hub["spoke_sheet_count"] >= 1
    assert hub["hub_sheet"] is not None


def test_hub_sheets_summary_empty_when_no_cross_sheet_match(tmp_path) -> None:
    """No cross-sheet findings → empty hub_sheets_summary."""
    write_minimal_xlsx(
        tmp_path / "unrelated.xlsx",
        [[1.0, 2.0], [3.0, 4.0]],
    )
    result = analyze_xlsx_root(
        tmp_path,
        PairForensicsParams(
            min_decimal_match_length=6,
            min_small_group_size=2,
            min_cross_sheet_matches=2,
            min_cross_sheet_fraction=0.5,
        ),
    )
    assert result.get("hub_sheets_summary", []) == []
