"""Build one row per funding round from the reports that describe it.

The rule for each field (which source wins, and why) is written out in
docs/SOURCE_OF_TRUTH.md; the constants below are that document in code.
"""
from __future__ import annotations

import pandas as pd

from . import parse as P
from .fx import inr_crore_to_usd_mn, rate_on
from .match import (BASIS_LABEL, DETAIL_LEVELS, attach_headlines, group_detail_reports, make_reports,
                    same_investor)
from .normalize import name_key, sector_from_description, sector_from_label, squash

# Source-of-truth order per field (first source that has a value wins).
AMOUNT_ORDER = ("entrackr", "inc42", "et", "vccircle")
NAME_ORDER = ("inc42", "entrackr", "et")
STAGE_ORDER = ("inc42", "entrackr", "et", "vccircle")
INVESTOR_ORDER = ("inc42", "entrackr", "et")

# The written rule for every field: (field, rule, why). The workbook's "Source rules" sheet and
# docs/SOURCE_OF_TRUTH.md are both produced from this list, so the text and the code cannot drift.
SOURCE_RULES = [
    ("Company", "The spelling most reports agree on, compared without case or punctuation. "
                "Ties go to the Inc42 deal table, then Entrackr, then ET.",
     "Typos differ by source; the spelling several sources share is the safest."),
    ("Announced", "The earliest publication date among the reports of the round.",
     "Later reports are follow-ups. The first report is closest to the announcement."),
    ("Amount (as reported)", "The first source that states an amount, in this order: Entrackr, Inc42, ET, VCCircle. "
                             "Every other figure is kept and compared, never averaged.",
     "Entrackr quotes the announcement currency and usually the dollar equivalent beside it. Inc42 converts "
     "everything to dollars. ET often reports a round before its final size is known. VCCircle is a headline only."),
    ("Amount (USD mn)", "Dollars as stated when the chosen source reports in dollars. Rupee figures are converted at "
                        "the ECB reference rate for the announcement date.",
     "One published rate per day makes every conversion reproducible."),
    ("Amount (INR cr)", "Rupees as stated when the chosen source reports in rupees, otherwise converted at the same rate.",
     "Kept beside the dollar figure so either can be checked against the source."),
    ("Stage", "The first source that names a stage: Inc42 table, Entrackr, ET, VCCircle.",
     "The Inc42 table has a dedicated round-type column. The others are read from prose."),
    ("Lead investors / Investors", "Every investor named by Inc42, Entrackr or ET, each listed once.",
     "Sources name different subsets of a syndicate, so the union is the fullest list."),
    ("Sector", "The Inc42 table's sector, with spelling variants merged. If Inc42 did not list the round: the "
               "sector Inc42 gave the same company in another round, else a keyword rule on the article's "
               "description of the company. Otherwise Unclassified.",
     "One taxonomy for all rounds. The basis is recorded per round in rounds.csv."),
    ("City", "The city an Entrackr or ET article says the company is based in. If no report of this round says "
             "so, the city from another round of the same company. Otherwise Unknown.",
     "Only stated locations are used. Nothing is filled in from memory."),
    ("Sources", "Every source with a matched report. A VCCircle headline can confirm a round but never creates one.",
     "A company name read from a URL is too rough to establish a round on its own."),
]

NUMERIC_COLUMNS = ["amount_usd_mn", "amount_inr_cr", "fx_inr_per_usd", "investor_count", "source_count",
                   "report_count", "amount_reports", "amount_low_usd_mn", "amount_high_usd_mn",
                   "amount_spread_pct", "date_spread_days"]
ROUND_COLUMNS = [
    "round_id", "company", "announced_date", "amount_usd_mn", "amount_inr_cr", "amount_as_reported",
    "amount_currency", "amount_source", "fx_inr_per_usd", "stage", "stage_group", "includes_debt",
    "lead_investors", "investors", "investor_count", "sector", "sector_basis", "subsector", "business_model",
    "city", "city_basis", "sources", "source_count", "report_count", "amount_reports", "amount_low_usd_mn",
    "amount_high_usd_mn", "amount_spread_pct", "date_spread_days", "name_variants", "source_urls",
]


def _first(reports: pd.DataFrame, column: str, order: tuple[str, ...], skip: tuple[str, ...] = ("",)) -> tuple[str, str]:
    """(value, source) from the first source in ``order`` that has one."""
    for source in order:
        for value in reports.loc[reports["source"] == source, column]:
            if pd.notna(value) and value not in skip:
                return value, source
    return "", ""


def _usd(report: pd.Series, rate: float) -> float | None:
    """A report's amount in USD million, using the round's reference rate for rupee figures."""
    if report["amount_currency"] == "USD" and pd.notna(report["amount_usd_mn"]):
        return float(report["amount_usd_mn"])
    if pd.notna(report["amount_inr_cr"]):
        return inr_crore_to_usd_mn(float(report["amount_inr_cr"]), rate)
    return float(report["amount_usd_mn"]) if pd.notna(report["amount_usd_mn"]) else None


def _union(reports: pd.DataFrame, column: str) -> list[str]:
    """Names from the detailed sources, without repeats ("Northstar" == "Northstar Capital")."""
    names: list[str] = []
    for source in INVESTOR_ORDER:
        for cell in reports.loc[reports["source"] == source, column]:
            for name in str(cell or "").split(";"):
                name = name.strip()
                if name and P.investor_key(name) and not any(same_investor(name, known) for known in names):
                    names.append(name)
    return names


def _display_name(reports: pd.DataFrame, detailed: pd.DataFrame) -> str:
    """The spelling most reports agree on; ties go to the Inc42 table, then Entrackr, then ET.

    Only detailed reports can supply the spelling. Headlines may vote for one,
    except VCCircle's: its URLs glue the first two words together.
    """
    spelled = {}                                         # spelling without case or punctuation -> [votes, name]
    for source in NAME_ORDER:
        for name in detailed.loc[detailed["source"] == source, "company_raw"]:
            spelled.setdefault(name_key(name), [0, name])
    for name in reports.loc[reports["source"] != "vccircle", "company_raw"]:
        if name_key(name) in spelled:
            spelled[name_key(name)][0] += 1
    return max(spelled.values(), key=lambda item: item[0])[1] if spelled else ""


def build_round(reports: pd.DataFrame, rates: pd.Series) -> dict:
    """Apply the source-of-truth rules to the reports of one round."""
    reports = reports.sort_values(["published_date", "record_id"])
    detailed = reports[reports["detail_level"].isin(DETAIL_LEVELS)]      # names and investors come from these
    announced = reports["published_date"].min()
    rate = rate_on(rates, announced)
    row = dict.fromkeys(ROUND_COLUMNS, "")

    row["company"] = _display_name(reports, detailed)
    row["announced_date"] = announced
    row["fx_inr_per_usd"] = round(rate, 2)

    # ---- amount: highest-priority source that states one; all figures kept for the cross-check
    usd_by_report = reports.apply(lambda r: _usd(r, rate), axis=1)
    stated = usd_by_report.dropna()
    for source in AMOUNT_ORDER:
        chosen = reports[(reports["source"] == source) & usd_by_report.notna()]
        if chosen.empty:
            continue
        pick = chosen.iloc[0]
        usd = usd_by_report.loc[pick.name]
        row["amount_usd_mn"] = round(usd, 3)
        row["amount_inr_cr"] = round(float(pick["amount_inr_cr"]) if pick["amount_currency"] == "INR"
                                     else usd * rate / 10.0, 2)
        row["amount_as_reported"] = pick["amount_text"]
        row["amount_currency"] = pick["amount_currency"]
        row["amount_source"] = source
        break
    row["amount_reports"] = int(reports.loc[stated.index, "source"].nunique()) if len(stated) else 0
    if len(stated):
        row["amount_low_usd_mn"] = round(stated.min(), 3)
        row["amount_high_usd_mn"] = round(stated.max(), 3)
        row["amount_spread_pct"] = round((stated.max() / stated.min() - 1) * 100, 1)

    # ---- stage, investors, sector, city
    stage, _ = _first(reports, "stage", STAGE_ORDER, skip=("", "Undisclosed"))
    row["stage"] = stage or "Undisclosed"
    row["stage_group"] = P.STAGE_GROUP[row["stage"]]
    row["includes_debt"] = bool(reports["includes_debt"].astype(bool).any())
    leads, everyone = _union(detailed, "lead_investors"), _union(detailed, "investors")
    everyone += [n for n in leads if not any(same_investor(n, known) for known in everyone)]
    row["lead_investors"] = "; ".join(leads)
    row["investors"] = "; ".join(everyone)
    row["investor_count"] = len(everyone)

    label, _ = _first(reports, "sector_raw", ("inc42",))
    sector = sector_from_label(label)
    if sector:
        row["sector"], row["sector_basis"] = sector, "source table (Inc42)"
    else:
        description, _ = _first(detailed, "sector_raw", ("entrackr", "et"))
        guessed = sector_from_description(description)
        row["sector"], row["sector_basis"] = (guessed, "keyword rule on description") if guessed else ("Unclassified", "")
    row["subsector"], _ = _first(reports, "subsector_raw", ("inc42",), skip=("", "-", "–", "—"))
    row["business_model"], _ = _first(reports, "business_model", ("inc42",), skip=("", "-", "–", "—"))
    city, _ = _first(detailed, "city", ("entrackr", "et"))
    row["city"], row["city_basis"] = (city, "stated in report") if city else ("Unknown", "")

    # ---- lineage
    sources = [s for s in ("entrackr", "inc42", "et", "vccircle") if (reports["source"] == s).any()]
    row["sources"] = " | ".join(sources)
    row["source_count"] = len(sources)
    row["report_count"] = len(reports)
    days = pd.to_datetime(reports["published_date"])
    row["date_spread_days"] = int((days.max() - days.min()).days)
    variants = list(dict.fromkeys(detailed["company_raw"].astype(str).str.strip()))
    row["name_variants"] = " | ".join(variants) if len({name_key(v) for v in variants}) > 1 else ""
    row["source_urls"] = " | ".join(reports.drop_duplicates("source")["url"])
    return row


INVESTOR_THRESHOLD = 8     # named as an investor in this many reports: treat the name as an investor


def _investors_as_companies(records: pd.DataFrame) -> pd.Series:
    """Reports whose "company" is really an investment firm ("Accel raises $550 million for India").

    No list of firms is kept by hand. A name counts as an investor when the
    reports themselves name it as one at least INVESTOR_THRESHOLD times.
    """
    usable = records[records["status"].isin(["ok", "confirm_only"]) & records["detail_level"].isin(DETAIL_LEVELS)]
    named = usable["investors"].str.split(";").explode().map(lambda name: P.investor_key(str(name)))
    frequent = set(named.value_counts()[lambda counts: counts >= INVESTOR_THRESHOLD].index) - {""}
    return records.index.isin(usable.index) & records["company_raw"].map(P.investor_key).isin(frequent)


def build_rounds(records: pd.DataFrame, rates: pd.Series):
    """Match reports and reconcile them.

    Returns (rounds, records with a ``round_id`` column, merge log, unmatched headlines).
    """
    records = records.copy()
    for column in ("amount_inr_cr", "amount_usd_mn"):
        records[column] = pd.to_numeric(records[column], errors="coerce")
    text_columns = records.columns.difference(["amount_inr_cr", "amount_usd_mn", "includes_debt"])
    records[text_columns] = records[text_columns].fillna("")
    records.loc[_investors_as_companies(records), "status"] = "excluded_investor_fund"
    # "confirm_only" reports (see extract.et_record) can join a round but cannot start one.
    usable = records[records["status"].isin(["ok", "confirm_only"])]
    starters = set(usable.index[usable["status"] == "ok"])

    reports = make_reports(usable, rates)
    detail = [r for r in reports if r.detail and r.idx in starters]
    headlines = [r for r in reports if not (r.detail and r.idx in starters)]
    membership, joined = group_detail_reports(detail)
    groups: dict[int, list] = {}
    for report in detail:
        groups.setdefault(membership[report.idx], []).append(report)
    attached = attach_headlines(headlines, groups)

    records["group"] = pd.Series(membership).reindex(records.index)
    for idx, (number, _, _) in attached.items():
        records.loc[idx, "group"] = number

    # Stable, readable ids: date of the first report plus the company name.
    round_ids: dict[int, str] = {}
    used: set[str] = set()
    for number, members in sorted(groups.items(), key=lambda item: (min(m.day for m in item[1]), item[0])):
        first = min(members, key=lambda m: (m.day, m.idx))
        base = f"R{first.day:%y%m%d}-{first.sq[:14] or 'unnamed'}"
        round_id, suffix = base, 2
        while round_id in used:
            round_id, suffix = f"{base}-{suffix}", suffix + 1
        used.add(round_id)
        round_ids[number] = round_id
    records["round_id"] = records["group"].map(round_ids).fillna("")

    rounds = []
    for round_id, members in records[records["round_id"] != ""].groupby("round_id", sort=False):
        row = build_round(members, rates)
        row["round_id"] = round_id
        rounds.append(row)
    rounds = pd.DataFrame(rounds, columns=ROUND_COLUMNS).sort_values(["announced_date", "round_id"]).reset_index(drop=True)
    for column in NUMERIC_COLUMNS:
        rounds[column] = pd.to_numeric(rounds[column], errors="coerce")
    rounds = fill_from_company(rounds)

    # ---- merge log: every report that was folded into an existing round, and on what basis
    log_rows = []
    basis_of = {**joined, **{idx: (basis, score) for idx, (_, basis, score) in attached.items()}}
    for idx, (basis, score) in basis_of.items():
        record = records.loc[idx]
        log_rows.append({"round_id": record["round_id"], "record_id": record["record_id"],
                         "source": record["source"], "company_in_report": record["company_raw"],
                         "match_basis": BASIS_LABEL[basis], "match_score": score})
    merge_log = pd.DataFrame(log_rows, columns=["round_id", "record_id", "source", "company_in_report",
                                                "match_basis", "match_score"]).sort_values(["round_id", "record_id"])

    unmatched = usable[(~usable.index.isin(membership)) & (~usable.index.isin(attached))]
    unmatched = unmatched[["record_id", "source", "published_date", "company_raw", "amount_text", "stage", "url"]]
    return rounds, records.drop(columns="group"), merge_log.reset_index(drop=True), unmatched.reset_index(drop=True)


def fill_from_company(rounds: pd.DataFrame) -> pd.DataFrame:
    """Use what is known about a company from its other rounds.

    A city stated in any report applies to the company's other rounds, and a sector taken from an
    Inc42 table replaces a keyword guess (or a blank) on the company's other rounds.
    """
    rounds = rounds.copy()
    key = rounds["company"].map(lambda name: squash(name_key(name)))
    other_round = "another round of the same company"

    stated = rounds[rounds["city_basis"] == "stated in report"].groupby(key)["city"].agg(lambda c: c.mode().iloc[0])
    fill = (rounds["city"] == "Unknown") & key.isin(stated.index)
    rounds.loc[fill, "city"] = key[fill].map(stated)
    rounds.loc[fill, "city_basis"] = other_round

    labelled = rounds[rounds["sector_basis"] == "source table (Inc42)"].groupby(key)["sector"].agg(
        lambda s: s.mode().iloc[0])
    fill = (rounds["sector_basis"] != "source table (Inc42)") & key.isin(labelled.index)
    rounds.loc[fill, "sector"] = key[fill].map(labelled)
    rounds.loc[fill, "sector_basis"] = other_round
    return rounds
