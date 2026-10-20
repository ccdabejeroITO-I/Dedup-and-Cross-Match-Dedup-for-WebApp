"""Convert CrossMatch result CSV ZIP into a memory-bounded Excel workbook.

This is an alternate downloadable representation of an already-classified job:
no matching, sorting, or classification is performed during conversion.
All CSV values are written literally as text to preserve leading zeroes,
long identity numbers, and protection against spreadsheet formulas.
"""
from __future__ import annotations

import csv
import io
import os
import threading
import uuid
import zipfile
from pathlib import Path

import xlsxwriter

from services.duplicate_group_styles import (
    GROUP_BANDS, MATCH_HIGHLIGHTS, GROUP_BORDER,
    group_band, highlight_kind, group_id_from_cell,
)

EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLUMNS = 16_384
EXCEL_MAX_CELL_LENGTH = 32_767
# Only a single conversion occurs at a time to prevent races / duplicate writes.
_excel_conversion_lock = threading.Lock()


def _unique_sheet_name(base: str, used: set[str]) -> str:
    base = str(base).strip().strip("'") or "RESULTS"
    base = ''.join('_' if char in '[]:*?/\\' else char for char in base)
    base = base[:31]
    name = base
    number = 2
    while name.casefold() in used:
        suffix = f' {number}'
        name = base[:31 - len(suffix)] + suffix
        number += 1
    used.add(name.casefold())
    return name


def csv_zip_to_excel(zip_path: Path, output_path: Path) -> Path:
    """Stream each CSV as an XLSX tab; create workbook atomically, keeping ZIP.

    Large sheets are split without dropping any rows. Formatting is light to
    support large files; native XLSX processing produces the richer format.
    """
    zip_path = Path(zip_path).resolve()
    output_path = Path(output_path).resolve()
    if not zip_path.is_file():
        raise FileNotFoundError("CSV ZIP results are no longer available.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with _excel_conversion_lock:
        if output_path.is_file() and output_path.stat().st_mtime_ns >= zip_path.stat().st_mtime_ns:
            return output_path
        temporary = output_path.with_name(f"{output_path.stem}.{uuid.uuid4().hex}.partial.xlsx")
        try:
            with zipfile.ZipFile(zip_path) as archive:
                csv_entries = [item for item in archive.infolist() if not item.is_dir() and item.filename.lower().endswith('.csv')]
                if not csv_entries:
                    raise ValueError("The result ZIP has no CSV worksheets.")
                # A group stripe is determined by appearance order. The row's
                # actual DedupType determines fuzzy/swap accents directly; no
                # expensive pre-scan or group-type buffer is needed.
                with xlsxwriter.Workbook(str(temporary), {
                    'constant_memory': True, 'use_zip64': True,
                    'strings_to_formulas': False, 'strings_to_urls': False,
                    'tmpdir': str(output_path.parent),
                }) as workbook:
                    header_style = workbook.add_format({
                        'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#17539A',
                        'valign': 'vcenter', 'text_wrap': True,
                    })
                    # Only the six SUMMARY values are counts; all other CSV
                    # cells remain literal text to protect beneficiary IDs.
                    count_fmt = workbook.add_format({'num_format': '#,##0'})
                    summary_count_labels = {
                        'File A', 'File B', 'Total File', 'Validated Records',
                        'Duplicate Records', 'Records for Review',
                    }
                    group_row_formats = {}
                    group_id_formats = {}
                    marker_formats = {}
                    for band_name, base_palette in GROUP_BANDS.items():
                        for highlight_name in (None, *MATCH_HIGHLIGHTS):
                            palette = MATCH_HIGHLIGHTS[highlight_name] if highlight_name else base_palette
                            for first in (False, True):
                                key = (band_name, highlight_name, first)
                                border = {'top': 2, 'top_color': GROUP_BORDER} if first else {}
                                group_row_formats[key] = workbook.add_format({
                                    'bg_color': palette['fill'], 'valign': 'vcenter', **border,
                                })
                                group_id_formats[key] = workbook.add_format({
                                    'bg_color': base_palette['strong'], 'font_color': base_palette['font'],
                                    'bold': True, 'valign': 'vcenter', **border,
                                })
                                marker_formats[key] = workbook.add_format({
                                    'bg_color': palette['strong'], 'font_color': palette['font'],
                                    'bold': True, 'valign': 'vcenter', **border,
                                })
                    used_names: set[str] = set()
                    group_ordinal = 0  # carries across numbered CSV parts
                    last_duplicate_gid = -1
                    for entry in csv_entries:
                        stem = Path(entry.filename).stem
                        if stem.strip().upper() in {"PROCESSING SUMMARY", "RUN_INFO"}:
                            continue  # non-result metadata; omit from Excel exports
                        with archive.open(entry) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as txt:
                            reader = csv.reader(txt)
                            try:
                                header = next(reader)
                            except StopIteration:
                                header = []
                            if len(header) > EXCEL_MAX_COLUMNS:
                                raise ValueError(f"{stem}: Excel supports at most {EXCEL_MAX_COLUMNS:,} columns. Use CSV ZIP.")
                            part = 1
                            title = stem
                            duplicate_csv = stem.upper().startswith('DUPLICATE RECORDS')
                            # User-facing export has no internal Group/Role columns.
                            # A reference row begins the next vertically grouped family.
                            dtype_col = header.index('Match Type') if duplicate_csv and 'Match Type' in header else -1
                            ws = None
                            last_gid = last_duplicate_gid if duplicate_csv else -1
                            row_number = 0
                            def new_sheet():
                                nonlocal ws, row_number, part
                                suffix = '' if part == 1 else f' {part}'
                                ws = workbook.add_worksheet(_unique_sheet_name(title[:31-len(suffix)] + suffix, used_names))
                                ws.hide_gridlines(2)
                                ws.freeze_panes(1, 0)
                                ws.set_default_row(16)
                                ws.set_row(0, 26)
                                for col, value in enumerate(header):
                                    if len(value) > EXCEL_MAX_CELL_LENGTH:
                                        raise ValueError(f"{stem}: header exceeds Excel cell length limit. Use CSV ZIP.")
                                    ws.write_string(0, col, value, header_style)
                                    ws.set_column(col, col, min(38, max(13, len(value) + 3)))
                                row_number = 1
                                part += 1
                            new_sheet()
                            for row in reader:
                                if len(row) > EXCEL_MAX_COLUMNS:
                                    raise ValueError(f"{stem}: too many columns for Excel. Use CSV ZIP.")
                                if row_number >= EXCEL_MAX_ROWS:
                                    new_sheet()
                                marker = (str(row[dtype_col] or '') if duplicate_csv
                                          and dtype_col >= 0 and len(row) > dtype_col else '')
                                first_row = bool(duplicate_csv and marker.startswith('REFERENCE (FILE '))
                                if duplicate_csv and (first_row or group_ordinal == 0):
                                    group_ordinal += 1
                                band_name = group_band(group_ordinal)
                                highlight_name = highlight_kind(marker) if duplicate_csv else None
                                style_key = (band_name, highlight_name, first_row)
                                for col, value in enumerate(row):
                                    if len(value) > EXCEL_MAX_CELL_LENGTH:
                                        raise ValueError(f"{stem}: a cell exceeds Excel's 32,767 character limit. Use CSV ZIP.")
                                    if value:
                                        if (stem.upper() == 'SUMMARY' and col == 1
                                                and row and row[0] in summary_count_labels
                                                and value.isascii() and value.isdecimal()):
                                            ws.write_number(row_number, col, int(value), count_fmt)
                                        else:
                                            # Preserve leading zeroes / IDs as literal text.
                                            # CSV has no colors; apply consistent group colors
                                            # only to the reconstructed Excel worksheet.
                                            cell_format = None
                                            if duplicate_csv:
                                                cell_format = group_row_formats[style_key]
                                                if col == dtype_col:
                                                    cell_format = marker_formats[style_key]
                                            ws.write_string(row_number, col, value, cell_format)
                                    elif duplicate_csv:
                                        # Explicitly style empty cells too so the entire
                                        # group row is colored edge-to-edge.
                                        ws.write_blank(row_number, col, None,
                                            group_row_formats[style_key])
                                row_number += 1
            os.replace(temporary, output_path)
            return output_path
        finally:
            temporary.unlink(missing_ok=True)
