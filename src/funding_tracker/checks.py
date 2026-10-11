"""The 18 automated data-quality checks.

Each check is a small function that returns the rows it flags. ``run_checks``
runs them all and returns two tables: one row per flag (the exception list)
and one row per check (the summary). Nothing is corrected automatically: a
flag sends a round to a person for review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations
from typing import Callable

import numpy as np
import pandas as pd

from .config import AMOUNT_TOLERANCE, START_DATE
from .fx import inr_crore_to_usd_mn, rate_on
from .match import DETAIL_LEVELS, same_investor
from .normalize import core_tokens, name_key

POSSIBLE_DUPLICATE_DAYS = 45     # two rounds of one company this close together get a second look
DATE_TOLERANCE_DAYS = 7          # reports of one round should not be further apart than this
FX_TOLERANCE = 0.10              # rupee and dollar figures in one report must imply a sane rate
MIN_AMOUNT_USD_MN = 0.01         # $10,000
MAX_AMOUNT_USD_MN = 2000.0       # $2 billion
MIN_ROUNDS_FOR_OUTLIERS = 10     # a stage needs this many amounts before outliers are judged


@dataclass(frozen=True)
class Check:
    id: str
    category: str
    name: str
    rule: str
    severity: str                                    # error | warning | info
    run: Callable[["Context"], pd.DataFrame]         # -> columns: round_id, detail


@dataclass
class Context:
    rounds: pd.DataFrame
    reports: pd.DataFrame        # source records that belong to a round, with ``usd`` filled in
    records: pd.DataFrame        # every source record
    rates: pd.Series
    today: date


def _flag(frame: pd.DataFrame, detail) -> pd.DataFrame:
    """Rows of ``frame`` as flags; ``detail`` is a string or a per-row function."""
    out = pd.DataFrame({"round_id": frame["round_id"].to_numpy()})
    out["detail"] = [detail(row) for _, row in frame.iterrows()] if callable(detail) else detail
    return out


# ------------------------------------------------------------- completeness
def missing_company(ctx: Context) -> pd.DataFrame:
    return _flag(ctx.rounds[ctx.rounds["company"].fillna("").str.strip() == ""], "no company name")


def missing_date(ctx: Context) -> pd.DataFrame:
    return _flag(ctx.rounds[pd.to_datetime(ctx.rounds["announced_date"], errors="coerce").isna()], "no usable date")


def missing_amount(ctx: Context) -> pd.DataFrame:
    return _flag(ctx.rounds[ctx.rounds["amount_usd_mn"].isna()], "amount not disclosed by any source")


def missing_stage(ctx: Context) -> pd.DataFrame:
    return _flag(ctx.rounds[ctx.rounds["stage"] == "Undisclosed"], "no source names the stage")


def missing_investors(ctx: Context) -> pd.DataFrame:
    return _flag(ctx.rounds[ctx.rounds["investor_count"] == 0], "no investor named")


def missing_sector(ctx: Context) -> pd.DataFrame:
    return _flag(ctx.rounds[ctx.rounds["sector"] == "Unclassified"], "no sector label and no keyword match")


def missing_city(ctx: Context) -> pd.DataFrame:
    return _flag(ctx.rounds[ctx.rounds["city"] == "Unknown"], "no report states where the company is based")


# --------------------------------------------------------------- duplicates
def duplicate_report(ctx: Context) -> pd.DataFrame:
    """The same article must not be counted twice (Inc42 table rows share a URL, so they are exempt)."""
    articles = ctx.records[ctx.records["detail_level"] != "table"]
    repeated = articles[articles.duplicated("url", keep=False)]
    return _flag(repeated, lambda r: f"{r['record_id']} repeats {r['url']}")


def possible_duplicate_round(ctx: Context) -> pd.DataFrame:
    rounds = ctx.rounds.copy()
    rounds["company_key"] = rounds["company"].map(lambda name: "".join(core_tokens(name_key(name))))
    rounds["day"] = pd.to_datetime(rounds["announced_date"])
    rounds = rounds[rounds["company_key"] != ""].sort_values(["company_key", "day"])
    previous = rounds.groupby("company_key")[["round_id", "day", "amount_usd_mn"]].shift(1)
    gap = (rounds["day"] - previous["day"]).dt.days
    close = rounds[gap <= POSSIBLE_DUPLICATE_DAYS].assign(
        prev_id=previous["round_id"], gap=gap, prev_amount=previous["amount_usd_mn"])
    return _flag(close, lambda r: f"{int(r['gap'])} days after {r['prev_id']} "
                                  f"(${r['prev_amount']:g} Mn then, ${r['amount_usd_mn']:g} Mn now)")


# ----------------------------------------------------------------- validity
def date_outside_window(ctx: Context) -> pd.DataFrame:
    days = pd.to_datetime(ctx.rounds["announced_date"], errors="coerce").dt.date
    outside = ctx.rounds[(days < START_DATE) | (days > ctx.today)]
    return _flag(outside, lambda r: f"{r['announced_date']} is outside {START_DATE} to {ctx.today}")


# ----------------------------------------------------------------- outliers
def amount_outlier_for_stage(ctx: Context) -> pd.DataFrame:
    """Tukey fences on log10(amount) within each stage."""
    known = ctx.rounds[ctx.rounds["amount_usd_mn"] > 0].assign(
        log_amount=lambda frame: np.log10(frame["amount_usd_mn"].astype(float)))
    flagged = []
    for stage, group in known[known["stage"] != "Undisclosed"].groupby("stage"):
        if len(group) < MIN_ROUNDS_FOR_OUTLIERS:
            continue
        q1, q3 = group["log_amount"].quantile([0.25, 0.75])
        low, high = 10 ** (q1 - 1.5 * (q3 - q1)), 10 ** (q3 + 1.5 * (q3 - q1))
        outside = group[(group["amount_usd_mn"] < low) | (group["amount_usd_mn"] > high)]
        flagged.append(_flag(outside, lambda r, s=stage, lo=low, hi=high:
                             f"${r['amount_usd_mn']:g} Mn is outside the usual {s} range (${lo:.2f}-{hi:.1f} Mn)"))
    return pd.concat(flagged) if flagged else pd.DataFrame(columns=["round_id", "detail"])


def implausible_amount(ctx: Context) -> pd.DataFrame:
    amount = ctx.rounds["amount_usd_mn"]
    odd = ctx.rounds[(amount < MIN_AMOUNT_USD_MN) | (amount > MAX_AMOUNT_USD_MN)]
    return _flag(odd, lambda r: f"${r['amount_usd_mn']:g} Mn is outside ${MIN_AMOUNT_USD_MN}-{MAX_AMOUNT_USD_MN:g} Mn")


# ------------------------------------------------------- units and currency
def inr_usd_mismatch(ctx: Context) -> pd.DataFrame:
    """A report that gives rupees and dollars must imply roughly the day's exchange rate."""
    both = ctx.reports[ctx.reports["amount_inr_cr"].notna() & ctx.reports["amount_usd_mn"].notna()].copy()
    both["implied"] = both["amount_inr_cr"] * 10.0 / both["amount_usd_mn"]
    both["reference"] = both["published_date"].map(lambda day: rate_on(ctx.rates, day))
    off = both[(both["implied"] / both["reference"] - 1).abs() > FX_TOLERANCE]
    return _flag(off, lambda r: f"{r['record_id']}: Rs {r['amount_inr_cr']:g} Cr and ${r['amount_usd_mn']:g} Mn imply "
                                f"{r['implied']:.1f} INR/USD, reference rate was {r['reference']:.1f}")


def scale_mismatch(ctx: Context) -> pd.DataFrame:
    """Amounts that differ by a factor of ~10, ~100 or ~1000 point to a lakh/crore or million/billion slip."""
    spread = ctx.reports[ctx.reports["usd"].notna()].groupby("round_id")["usd"].agg(["min", "max"])
    power = np.log10(spread["max"] / spread["min"])
    slipped = spread[((power - power.round()).abs() < 0.045) & (power.round() >= 1)]
    return _flag(slipped.reset_index(), lambda r: f"reports range from ${r['min']:g} Mn to ${r['max']:g} Mn "
                                                  f"(a factor of about {r['max'] / r['min']:.0f})")


# ------------------------------------------------------------- cross-source
def amount_disagreement(ctx: Context) -> pd.DataFrame:
    rounds = ctx.rounds[ctx.rounds["amount_spread_pct"] > AMOUNT_TOLERANCE * 100]
    figures = ctx.reports[ctx.reports["usd"].notna()].groupby("round_id").apply(
        lambda g: "; ".join(f"{s} ${u:g} Mn" for s, u in zip(g["source"], g["usd"].round(2))), include_groups=False)
    return _flag(rounds, lambda r: f"spread {r['amount_spread_pct']:g}%: {figures.get(r['round_id'], '')}")


def date_disagreement(ctx: Context) -> pd.DataFrame:
    rounds = ctx.rounds[ctx.rounds["date_spread_days"] > DATE_TOLERANCE_DAYS]
    return _flag(rounds, lambda r: f"reports are {r['date_spread_days']} days apart")


def lead_investor_disagreement(ctx: Context) -> pd.DataFrame:
    """Two detailed sources each name a lead investor, and they have none in common."""
    flagged = []
    detail = ctx.reports[ctx.reports["detail_level"].isin(DETAIL_LEVELS) & (ctx.reports["lead_investors"] != "")]
    for round_id, group in detail.groupby("round_id"):
        leads = {source: [n.strip() for cell in rows for n in cell.split(";") if n.strip()]
                 for source, rows in group.groupby("source")["lead_investors"]}
        apart = [(a, b) for a, b in combinations(leads, 2)
                 if not any(same_investor(x, y) for x in leads[a] for y in leads[b])]
        if apart:
            names = " | ".join(f"{source}: {', '.join(names)}" for source, names in leads.items())
            flagged.append({"round_id": round_id, "detail": names})
    return pd.DataFrame(flagged, columns=["round_id", "detail"])


def stage_disagreement(ctx: Context) -> pd.DataFrame:
    detail = ctx.reports[ctx.reports["detail_level"].isin(DETAIL_LEVELS) & (ctx.reports["stage"] != "Undisclosed")]
    stages = detail.groupby("round_id").apply(
        lambda g: "; ".join(f"{s}: {t}" for s, t in g.drop_duplicates(["source", "stage"])[["source", "stage"]].values)
        if g["stage"].nunique() > 1 else "", include_groups=False)
    stages = stages[stages != ""]
    return pd.DataFrame({"round_id": stages.index, "detail": stages.to_numpy()})


CHECKS = [
    Check("C01", "Nulls", "Company name missing", "company is blank", "error", missing_company),
    Check("C02", "Nulls", "Announcement date missing", "announced_date is blank or not a date", "error", missing_date),
    Check("C03", "Nulls", "Amount undisclosed", "no source reports an amount", "info", missing_amount),
    Check("C04", "Nulls", "Stage undisclosed", "no source names the round stage", "info", missing_stage),
    Check("C05", "Nulls", "No investor named", "no source names an investor", "info", missing_investors),
    Check("C06", "Nulls", "Sector unclassified", "no sector label in a source table and no keyword match", "warning",
          missing_sector),
    Check("C07", "Nulls", "City unknown", "no report says where the company is based", "info", missing_city),
    Check("C08", "Duplicates", "Duplicate report", "one article URL appears more than once in the source records",
          "error", duplicate_report),
    Check("C09", "Duplicates", "Possible duplicate round",
          f"same company has another round within {POSSIBLE_DUPLICATE_DAYS} days", "warning", possible_duplicate_round),
    Check("C10", "Validity", "Date outside tracking window", f"announced_date before {START_DATE} or in the future",
          "error", date_outside_window),
    Check("C11", "Outliers", "Amount outlier for its stage",
          f"log10(amount) beyond 1.5 x IQR for the stage (stages with at least {MIN_ROUNDS_FOR_OUTLIERS} amounts)",
          "warning", amount_outlier_for_stage),
    Check("C12", "Outliers", "Implausible amount",
          f"amount below ${MIN_AMOUNT_USD_MN} Mn or above ${MAX_AMOUNT_USD_MN:g} Mn", "warning", implausible_amount),
    Check("C13", "Units", "INR/USD mismatch",
          f"a report's rupee and dollar figures imply a rate more than {FX_TOLERANCE:.0%} from the reference rate",
          "warning", inr_usd_mismatch),
    Check("C14", "Units", "Lakh/crore or million/billion mismatch",
          "reports of one round differ by a factor of about 10, 100 or 1,000", "error", scale_mismatch),
    Check("C15", "Cross-source", "Amount disagreement",
          f"highest and lowest reported amount differ by more than {AMOUNT_TOLERANCE:.0%}", "warning",
          amount_disagreement),
    Check("C16", "Cross-source", "Date disagreement",
          f"reports of one round are more than {DATE_TOLERANCE_DAYS} days apart", "info", date_disagreement),
    Check("C17", "Cross-source", "Lead investor disagreement",
          "two detailed sources each name a lead investor and have none in common", "warning",
          lead_investor_disagreement),
    Check("C18", "Cross-source", "Stage disagreement", "two detailed sources state different stages", "warning",
          stage_disagreement),
]


def run_checks(rounds: pd.DataFrame, records: pd.DataFrame, rates: pd.Series,
               today: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run all checks. Returns (exceptions: one row per flag, summary: one row per check)."""
    reports = records[records["round_id"] != ""].copy()
    announced = reports["round_id"].map(rounds.set_index("round_id")["announced_date"])
    rate = announced.map(lambda day: rate_on(rates, day))
    in_usd = (reports["amount_currency"] == "USD") & reports["amount_usd_mn"].notna()
    reports["usd"] = np.where(in_usd, reports["amount_usd_mn"],
                              inr_crore_to_usd_mn(reports["amount_inr_cr"], rate))
    reports["usd"] = reports["usd"].fillna(reports["amount_usd_mn"])
    ctx = Context(rounds=rounds, reports=reports, records=records, rates=rates, today=today or date.today())

    lookup = rounds.set_index("round_id")[["company", "announced_date"]]
    exceptions, summary = [], []
    for check in CHECKS:
        flags = check.run(ctx).drop_duplicates()
        flags.insert(0, "check_id", check.id)
        flags.insert(1, "check", check.name)
        flags.insert(2, "severity", check.severity)
        exceptions.append(flags)
        summary.append({"check_id": check.id, "category": check.category, "check": check.name, "rule": check.rule,
                        "severity": check.severity, "rounds_checked": len(rounds),
                        "rounds_flagged": flags["round_id"].nunique()})
    exceptions = pd.concat(exceptions, ignore_index=True)
    exceptions = exceptions.join(lookup, on="round_id")
    exceptions = exceptions[["check_id", "check", "severity", "round_id", "company", "announced_date", "detail"]]
    summary = pd.DataFrame(summary)
    summary["share_flagged"] = (summary["rounds_flagged"] / summary["rounds_checked"].clip(lower=1)).round(4)
    return exceptions.sort_values(["check_id", "announced_date", "round_id", "detail"]).reset_index(drop=True), summary
