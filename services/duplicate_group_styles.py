"""Excel-only duplicate-group striping and individual match-type highlights.

The displayed group order determines white/gray stripes. FUZZY, SWAP and
FUZZY_SWAP receive distinct row fills; DedupGroup retains the base stripe to
make group boundaries identifiable even in mixed groups. Classification is
never affected by these presentation helpers.
"""
from __future__ import annotations

GROUP_BANDS = {
    "WHITE": {"fill": "#FFFFFF", "strong": "#EBF0F5", "font": "#253B53"},
    "GRAY": {"fill": "#F1F3F6", "strong": "#DFE5EC", "font": "#253B53"},
}

MATCH_HIGHLIGHTS = {
    "FUZZY": {"fill": "#FFF1C4", "strong": "#FFE099", "font": "#865600"},
    "SWAP": {"fill": "#EEE2FC", "strong": "#DAC3F7", "font": "#613392"},
    "FUZZY_SWAP": {"fill": "#FFE4EE", "strong": "#FFC3D8", "font": "#8C2856"},
}

GROUP_BORDER = "#A5B2C2"


def group_band(ordinal: int) -> str:
    """1-based displayed group number; non-consecutive group IDs are fine."""
    return "WHITE" if int(ordinal) % 2 == 1 else "GRAY"


def highlight_kind(label: str) -> str | None:
    """Highlight only actual fuzzy/swap candidate rows, not reference/exact."""
    name = str(label or "").upper().strip()
    if "SWAP" in name and "FUZZY" in name:
        return "FUZZY_SWAP"
    if "SWAP" in name:
        return "SWAP"
    if "FUZZY" in name:
        return "FUZZY"
    return None


def group_id_from_cell(value: object) -> int:
    text = str(value or "").strip()
    if not text.upper().startswith("GROUP-"):
        return 0
    try:
        return int(text[6:])
    except ValueError:
        return 0
