"""The two stages of the tracker.

``collect`` talks to the sources and writes one row per report.
``build``   works offline from those rows: match, reconcile, check, report.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from .checks import run_checks
from .config import INTERIM, PROCESSED, RAW, REPORTS, SOURCES, START_DATE
from .extract import RECORD_COLUMNS, TOTAL_COLUMNS, extract
from .fx import load_rates, update_rates
from .http import Fetcher
from .reconcile import build_rounds
from .sources import discover, download_pages

log = logging.getLogger(__name__)

RECORDS_FILE = INTERIM / "source_records.csv"
TOTALS_FILE = INTERIM / "weekly_totals.csv"
OVERLAP_DAYS = 10      # an update re-reads this many days before the last report it already holds
WATCH_COLUMNS = ["published_date", "source", "company_raw", "amount_text", "stage", "url"]
MATCHED_COLUMNS = ["round_id", "record_id", "source", "detail_level", "published_date", "company_raw", "amount_text",
                   "amount_currency", "amount_inr_cr", "amount_usd_mn", "stage", "lead_investors", "url"]


def read_records() -> pd.DataFrame:
    records = pd.read_csv(RECORDS_FILE, dtype=str, keep_default_na=False)
    for column in ("amount_inr_cr", "amount_usd_mn"):
        records[column] = pd.to_numeric(records[column], errors="coerce")
    records["includes_debt"] = records["includes_debt"].str.lower() == "true"
    return records


def collect(full: bool = False, offline: bool = False) -> pd.DataFrame:
    """Read the sources and update ``data/interim/source_records.csv``.

    A normal run only looks at the days since the last report on file (plus an
    overlap, because articles are sometimes edited). ``full=True`` re-reads
    everything from the start date; with ``offline=True`` only cached pages are
    used, which is how the records are rebuilt after a parsing rule changes.
    """
    until = date.today()
    known = read_records() if RECORDS_FILE.exists() and not full else None
    known_totals = pd.read_csv(TOTALS_FILE, dtype=str, keep_default_na=False) \
        if TOTALS_FILE.exists() and not full else None
    since = START_DATE
    if known is not None and not known.empty:
        since = max(START_DATE, date.fromisoformat(known["published_date"].max()) - timedelta(days=OVERLAP_DAYS))
    log.info("collecting %s to %s%s", since, until, " (offline)" if offline else "")

    fetcher = Fetcher(RAW, offline=offline)
    new_records, new_totals = [], []
    for source in SOURCES:
        candidates = discover(source, fetcher, since, until)
        if not offline:
            download_pages(candidates, fetcher)
        records, totals = extract(candidates, fetcher)
        log.info("%s: %s candidates -> %s records (%s usable)", source, len(candidates), len(records),
                 int(records["status"].isin(["ok", "confirm_only"]).sum()))
        if records.empty:
            # Shows up as a warning in the GitHub Actions run. Older records are kept either way.
            print(f"::warning::{source} returned no reports for {since} to {until}; the site may be blocking this run")
        new_records.append(records)
        new_totals.append(totals)

    records = pd.concat(([known] if known is not None else []) + new_records, ignore_index=True)
    records = records.drop_duplicates("record_id", keep="last").sort_values(["published_date", "record_id"])
    # For a story that was left out, the "company" can be a stretch of its headline. Only names are kept.
    left_out = ~records["status"].isin(["ok", "confirm_only", "excluded_not_closed"])
    wordy = records["company_raw"].fillna("").str.split().str.len() > 4
    records.loc[left_out & wordy, "company_raw"] = ""
    totals = pd.concat(([known_totals] if known_totals is not None else []) + new_totals, ignore_index=True)
    totals = totals.drop_duplicates("url", keep="last").sort_values(["published_date", "source", "url"])
    INTERIM.mkdir(parents=True, exist_ok=True)
    records[RECORD_COLUMNS].to_csv(RECORDS_FILE, index=False)
    totals[TOTAL_COLUMNS].to_csv(TOTALS_FILE, index=False)
    log.info("%s records on file (%s requests made)", len(records), fetcher.requests_made)
    return records


def coverage_table(rounds: pd.DataFrame, totals: pd.DataFrame) -> pd.DataFrame:
    """Compare the tracker with Entrackr's own weekly count of funded startups (a control total)."""
    weekly = totals[totals["source"] == "entrackr"].copy()
    if weekly.empty:
        return pd.DataFrame(columns=["week_start", "week_end", "entrackr_deals", "tracked_rounds", "difference",
                                     "entrackr_usd_mn", "tracked_usd_mn"])
    weekly["week_end"] = pd.to_datetime(weekly["period_end"].where(weekly["period_end"] != "", weekly["published_date"]))
    start = pd.to_datetime(weekly["period_start"].where(weekly["period_start"] != ""), errors="coerce")
    weekly["week_start"] = start.fillna(weekly["week_end"] - pd.Timedelta(days=6))
    days = pd.to_datetime(rounds["announced_date"])
    rows = []
    for _, week in weekly.sort_values("week_start").iterrows():
        inside = rounds[(days >= week["week_start"]) & (days <= week["week_end"])]
        reported = int(float(week["deals_reported"]))
        rows.append({
            "week_start": week["week_start"].date().isoformat(), "week_end": week["week_end"].date().isoformat(),
            "entrackr_deals": reported, "tracked_rounds": len(inside), "difference": len(inside) - reported,
            "entrackr_usd_mn": round(float(week["amount_usd_mn_reported"]), 2),
            "tracked_usd_mn": round(float(inside["amount_usd_mn"].sum()), 2),
        })
    return pd.DataFrame(rows).drop_duplicates(["week_start", "week_end"])


def build(offline: bool = False, today: date | None = None) -> dict[str, pd.DataFrame]:
    """Match, reconcile and check the records on file, then write every output."""
    from .excel import write_workbook      # imported here so that collecting does not need openpyxl
    from .report import write_summary

    records = read_records()
    if not offline:
        update_rates()
    rates = load_rates()
    rounds, records, merge_log, unmatched = build_rounds(records, rates)
    exceptions, check_summary = run_checks(rounds, records, rates, today)
    flags = exceptions.groupby("round_id")["check_id"].agg(lambda ids: " ".join(sorted(set(ids))))
    rounds["flags"] = rounds["round_id"].map(flags).fillna("")
    totals = pd.read_csv(TOTALS_FILE, dtype=str, keep_default_na=False) if TOTALS_FILE.exists() \
        else pd.DataFrame(columns=TOTAL_COLUMNS)
    coverage = coverage_table(rounds, totals)

    tables = {"rounds": rounds, "records": records, "merge_log": merge_log, "unmatched_headlines": unmatched,
              "exceptions": exceptions, "checks_summary": check_summary, "coverage": coverage}
    PROCESSED.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    rounds.to_csv(PROCESSED / "rounds.csv", index=False)
    matched = records.loc[records["round_id"] != "", MATCHED_COLUMNS].sort_values(["round_id", "published_date"])
    matched.to_csv(PROCESSED / "reports_matched.csv", index=False)
    merge_log.to_csv(PROCESSED / "merge_log.csv", index=False)
    unmatched.to_csv(PROCESSED / "unmatched_headlines.csv", index=False)
    # Rounds reported as under way ("in talks", "to raise"): not counted, but worth watching.
    watch = records.loc[records["status"] == "excluded_not_closed", WATCH_COLUMNS]
    watch.sort_values("published_date", ascending=False).to_csv(PROCESSED / "not_closed_watchlist.csv", index=False)
    exceptions.to_csv(PROCESSED / "exceptions.csv", index=False)
    check_summary.to_csv(PROCESSED / "checks_summary.csv", index=False)
    coverage.to_csv(PROCESSED / "weekly_coverage.csv", index=False)
    write_workbook(tables, REPORTS / "funding_tracker.xlsx", rates)
    write_summary(tables, REPORTS / "summary.md")
    log.info("%s rounds from %s reports; %s flags raised", len(rounds), int((records["round_id"] != "").sum()),
             len(exceptions))
    return tables
