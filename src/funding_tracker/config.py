"""Paths and settings shared by every stage of the pipeline."""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA = ROOT / "data"
RAW = DATA / "raw"              # downloaded pages (not committed)
INTERIM = DATA / "interim"      # one row per source report
PROCESSED = DATA / "processed"  # one row per funding round
REFERENCE = DATA / "reference"  # FX rates and lookup tables
REPORTS = ROOT / "reports"      # Excel workbook and summary

START_DATE = date(2025, 1, 1)   # first announcement date tracked

SOURCES = ("entrackr", "inc42", "et", "vccircle")
SOURCE_LABELS = {
    "entrackr": "Entrackr",
    "inc42": "Inc42",
    "et": "The Economic Times",
    "vccircle": "VCCircle",
}

# Cross-source tolerance: sources "disagree" on the amount when the highest and
# lowest reported figures for one round differ by more than this share.
AMOUNT_TOLERANCE = 0.10
