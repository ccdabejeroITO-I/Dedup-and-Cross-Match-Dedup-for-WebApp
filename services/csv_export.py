"""Memory-bounded conversion of an already-completed result workbook to CSV ZIP."""
from __future__ import annotations

import csv
import io
import os
import re
import uuid
import zipfile
from pathlib import Path

from openpyxl import load_workbook


def _csv_safe(value: object) -> str:
    """Protect spreadsheet users against formula execution on CSV re-import."""
    if value is None:
        return ""
    text = str(value)
    # Preserve plain values where possible. Only neutralize CSV cells that a
    # spreadsheet may parse as a formula, including leading whitespace variants.
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def workbook_to_csv_zip(workbook: Path, destination: Path) -> Path:
    """Write a ZIP with one UTF-8-BOM CSV for every worksheet, atomically.

    openpyxl's read-only iterator and per-sheet ZIP streaming avoid retaining
    complete exported worksheets in Python memory. The original workbook is
    left unchanged. CSV is an alternate *view* of the Excel result.
    """
    workbook = Path(workbook).resolve()
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".partial")
    book = load_workbook(workbook, read_only=True, data_only=True, keep_links=False)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
            used: set[str] = set()
            for index, sheet in enumerate(book.worksheets, 1):
                if sheet.title.strip().upper() in {"PROCESSING SUMMARY", "RUN_INFO"}:
                    continue  # non-result metadata; omit from CSV ZIP exports
                stem = re.sub(r"[^a-zA-Z0-9_. -]", "_", sheet.title).strip(" .") or f"Sheet_{index}"
                name = stem + ".csv"
                n = 2
                while name.lower() in used:
                    name = f"{stem}_{n}.csv"
                    n += 1
                used.add(name.lower())
                with archive.open(name, "w", force_zip64=True) as raw:
                    with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                        writer = csv.writer(stream)
                        for row in sheet.iter_rows(values_only=True):
                            writer.writerow([_csv_safe(cell) for cell in row])
        os.replace(temporary, destination)
        return destination
    finally:
        book.close()
        temporary.unlink(missing_ok=True)
