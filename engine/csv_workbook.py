"""Minimal streaming CSV ZIP writer for the v6.31 export's supported worksheet API.

Each sheet is a separate temporary CSV file, allowing the original export to
interleave writes across SUMMARY, CLEAN, REVIEW and DUPLICATE
without holding whole million-row sheets in RAM. Formatting is omitted; row
content, grouping, categories and engine integrity counters are untouched.
"""
from __future__ import annotations

import csv
import re
import tempfile
import zipfile
from pathlib import Path


def safe_csv_cell(value):
    if value is None:
        return ""
    text = str(value)
    # A CSV file might be opened directly in Excel or another spreadsheet.
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class CsvSheet:
    def __init__(self, folder: Path, title: str, index: int):
        self.title = title
        self.filename = re.sub(r"[^a-zA-Z0-9_. -]", "_", title).strip(" .") or f"Sheet_{index}"
        self.filename += ".csv"
        self.path = folder / f"{index:03d}.csv"
        self._file = self.path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.writer(self._file)
        self._row = -1
        self._values = []
        self._closed = False

    def _flush(self):
        if self._row >= 0:
            self._writer.writerow([safe_csv_cell(v) for v in self._values])

    def _position(self, row: int):
        row = int(row)
        if row < 0 or row < self._row:
            raise ValueError(f"Non-streaming sheet write in {self.title}: {row} after {self._row}")
        if row > self._row:
            self._flush()
            for _ in range(self._row + 1, row):
                self._writer.writerow([])
            self._row = row
            self._values = []

    def write(self, row: int, col: int, value, *args, **kwargs):
        self._position(row)
        col = int(col)
        if col < 0:
            raise ValueError("Negative sheet column")
        if len(self._values) <= col:
            self._values.extend([""] * (col + 1 - len(self._values)))
        self._values[col] = value
        return 0

    def write_number(self, row: int, col: int, value, *args, **kwargs):
        return self.write(row, col, value)

    def write_row(self, row: int, col: int, values, *args, **kwargs):
        # One positioning/buffer operation per record, not per cell.
        self._position(row)
        col = int(col)
        if col < 0:
            raise ValueError("Negative sheet column")
        items = list(values)
        end = col + len(items)
        if len(self._values) < end:
            self._values.extend([""] * (end - len(self._values)))
        self._values[col:end] = items
        return 0

    # The original Excel export uses these for visual-only formatting.
    def hide_gridlines(self, *args, **kwargs): pass
    def set_column(self, *args, **kwargs): pass
    def set_row(self, *args, **kwargs): pass
    def freeze_panes(self, *args, **kwargs): pass
    def autofilter(self, *args, **kwargs): pass
    def conditional_format(self, *args, **kwargs): pass

    def close(self):
        if not self._closed:
            self._flush()
            self._file.close()
            self._closed = True


class CsvWorkbook:
    """Drop-in worksheet sink for the v6.31 export_results streaming path."""
    def __init__(self, output_path, *args, **kwargs):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="crossmatch_csv_", dir=self.output_path.parent
        )
        self._dir = Path(self._temporary.name)
        self._sheets = []
        self._closed = False

    def use_zip64(self): pass
    def set_properties(self, *args, **kwargs): pass
    def add_format(self, *args, **kwargs): return None

    def add_worksheet(self, name):
        if self._closed:
            raise RuntimeError("Cannot add sheet after closing CSV ZIP")
        sheet = CsvSheet(self._dir, name, len(self._sheets) + 1)
        self._sheets.append(sheet)
        return sheet

    def close(self):
        if self._closed:
            return
        try:
            for sheet in self._sheets:
                sheet.close()
            used = set()
            with zipfile.ZipFile(self.output_path, "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=1, allowZip64=True) as archive:
                for sheet in self._sheets:
                    filename = sheet.filename
                    suffix = 2
                    while filename.casefold() in used:
                        filename = f"{Path(sheet.filename).stem}_{suffix}.csv"
                        suffix += 1
                    used.add(filename.casefold())
                    archive.write(sheet.path, filename)
        finally:
            self._temporary.cleanup()
            self._closed = True
