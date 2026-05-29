#!/usr/bin/env python3
"""Profile XLSX source-data files without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import xml.etree.ElementTree as ET


SHEET_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
CELL_REF_RE = re.compile(r"([A-Z]+)([0-9]+)")


def col_to_idx(col: str) -> int:
    value = 0
    for ch in col:
        value = value * 26 + ord(ch) - 64
    return value


def read_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def text_of(element: ET.Element | None) -> str:
    return "".join(element.itertext()) if element is not None else ""


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = read_xml(zf, "xl/sharedStrings.xml")
    except KeyError:
        return []
    return [text_of(item) for item in root.findall("a:si", SHEET_NS)]


def workbook_sheets(zf: zipfile.ZipFile) -> list[dict]:
    workbook = read_xml(zf, "xl/workbook.xml")
    rels = read_xml(zf, "xl/_rels/workbook.xml.rels")
    rid_to_target = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("rel:Relationship", REL_NS)
    }
    sheets = []
    for sheet in workbook.findall("a:sheets/a:sheet", SHEET_NS):
        rid = sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = rid_to_target.get(rid, "")
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target.startswith("xl/"):
            path = target
        else:
            path = f"xl/{target}"
        sheets.append(
            {
                "name": sheet.attrib.get("name"),
                "sheet_id": sheet.attrib.get("sheetId"),
                "state": sheet.attrib.get("state", "visible"),
                "path": path,
            }
        )
    return sheets


def parse_decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    value = str(raw).strip().replace(",", "")
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def normalized_number(value: Decimal) -> str:
    return str(value.normalize())


def terminal_digit(raw: str | None) -> str | None:
    if raw is None:
        return None
    match = re.search(r"(\d)(?:\.0+)?$", str(raw).strip())
    return match.group(1) if match else None


def fractional_part(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if "." not in value:
        return None
    fraction = value.split(".", 1)[1].rstrip("0")
    return fraction if fraction else "0"


def parse_cell(cell: ET.Element, shared: list[str]) -> dict:
    ref = cell.attrib.get("r", "")
    match = CELL_REF_RE.match(ref)
    col = col_to_idx(match.group(1)) if match else None
    row = int(match.group(2)) if match else None
    cell_type = cell.attrib.get("t") or "n"
    formula = cell.find("a:f", SHEET_NS)
    value_node = cell.find("a:v", SHEET_NS)
    inline = cell.find("a:is", SHEET_NS)
    raw = text_of(value_node)
    value = raw
    if cell_type == "s" and raw:
        try:
            value = shared[int(raw)]
        except (ValueError, IndexError):
            value = raw
    elif cell_type == "inlineStr":
        value = text_of(inline)
    numeric = None if cell_type in {"s", "str", "inlineStr", "b"} else parse_decimal(raw)
    return {
        "ref": ref,
        "row": row,
        "col": col,
        "type": cell_type,
        "raw": raw,
        "value": value,
        "numeric": numeric,
        "formula": text_of(formula) if formula is not None else None,
    }


def profile_sheet(zf: zipfile.ZipFile, sheet: dict, shared: list[str]) -> dict:
    root = read_xml(zf, sheet["path"])
    formulas = []
    numeric = []
    rows: dict[int, list[dict]] = defaultdict(list)
    cell_count = 0
    for cell in root.findall(".//a:sheetData/a:row/a:c", SHEET_NS):
        cell_count += 1
        parsed = parse_cell(cell, shared)
        if parsed["formula"]:
            formulas.append({"ref": parsed["ref"], "formula": parsed["formula"]})
        if parsed["numeric"] is not None:
            numeric.append(parsed)
            if parsed["row"] is not None:
                rows[parsed["row"]].append(parsed)

    repeated_values = Counter(normalized_number(item["numeric"]) for item in numeric)
    terminal_digits = Counter(
        digit for digit in (terminal_digit(item["raw"]) for item in numeric) if digit
    )
    fractional_parts = Counter(
        part for part in (fractional_part(item["raw"]) for item in numeric) if part
    )

    duplicate_rows = []
    row_values = []
    for row_idx, row_cells in rows.items():
        values = [
            normalized_number(item["numeric"])
            for item in sorted(row_cells, key=lambda value: value["col"] or 0)
        ]
        if len(values) >= 3:
            row_values.append((tuple(values), row_idx))
    row_counter = Counter(values for values, _ in row_values)
    for values, count in row_counter.most_common(20):
        if count > 1:
            duplicate_rows.append(
                {
                    "count": count,
                    "values": list(values),
                    "rows": [row for row_values_, row in row_values if row_values_ == values][:30],
                }
            )

    terminal_total = sum(terminal_digits.values())
    return {
        "name": sheet["name"],
        "path": sheet["path"],
        "state": sheet["state"],
        "cell_count": cell_count,
        "numeric_cell_count": len(numeric),
        "formula_count": len(formulas),
        "formula_sample": formulas[:30],
        "top_repeated_numeric_values": [
            {"value": value, "count": count}
            for value, count in repeated_values.most_common(30)
            if count > 1
        ],
        "terminal_digit_counts": dict(terminal_digits),
        "terminal_0_or_5_rate": (
            (terminal_digits.get("0", 0) + terminal_digits.get("5", 0)) / terminal_total
            if terminal_total
            else None
        ),
        "top_repeated_fractional_parts": [
            {"fractional_part": value, "count": count}
            for value, count in fractional_parts.most_common(30)
            if count > 1
        ],
        "duplicate_numeric_rows": duplicate_rows,
    }


def profile_workbook(path: Path) -> dict:
    workbook = {"file": str(path), "file_name": path.name, "sheets": [], "error": None}
    try:
        with zipfile.ZipFile(path) as zf:
            shared = shared_strings(zf)
            for sheet in workbook_sheets(zf):
                workbook["sheets"].append(profile_sheet(zf, sheet, shared))
    except Exception as exc:  # keep profiling other workbooks
        workbook["error"] = f"{type(exc).__name__}: {exc}"
    return workbook


def summarize(workbooks: list[dict]) -> dict:
    terminal_digits: Counter[str] = Counter()
    for workbook in workbooks:
        for sheet in workbook["sheets"]:
            terminal_digits.update(sheet["terminal_digit_counts"])
    terminal_total = sum(terminal_digits.values())
    return {
        "workbook_count": len(workbooks),
        "sheet_count": sum(len(workbook["sheets"]) for workbook in workbooks),
        "cell_count": sum(
            sheet["cell_count"] for workbook in workbooks for sheet in workbook["sheets"]
        ),
        "numeric_cell_count": sum(
            sheet["numeric_cell_count"]
            for workbook in workbooks
            for sheet in workbook["sheets"]
        ),
        "formula_count": sum(
            sheet["formula_count"] for workbook in workbooks for sheet in workbook["sheets"]
        ),
        "terminal_digit_counts": dict(terminal_digits),
        "terminal_0_or_5_rate": (
            (terminal_digits.get("0", 0) + terminal_digits.get("5", 0)) / terminal_total
            if terminal_total
            else None
        ),
        "workbooks_with_errors": [
            workbook["file_name"] for workbook in workbooks if workbook["error"]
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile XLSX source-data files.")
    parser.add_argument("xlsx_root", help="Directory containing .xlsx files.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.xlsx_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    workbooks = [profile_workbook(path) for path in sorted(root.glob("*.xlsx"))]
    result = {"summary": summarize(workbooks), "workbooks": workbooks}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
