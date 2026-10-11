"""Decide which source reports describe the same funding round.

Step 1 groups the detailed reports (Entrackr and ET articles, Inc42 table rows)
into rounds. Step 2 attaches headline-level reports (VCCircle, and any ET story
whose page could not be read) to those rounds. A headline on its own never
creates a round: the name in a URL slug is too rough for that, so unmatched
headlines go to a review list instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from rapidfuzz import fuzz

from . import parse as P
from .fx import inr_crore_to_usd_mn, rate_on
from .normalize import core_tokens, name_key, squash

SHORT_WINDOW = 10      # days: same name this close together is the same round
LONG_WINDOW = 30       # days: allowed only when the amounts also agree
FUZZY_SCORE = 90       # minimum rapidfuzz ratio for a spelling variant
AMOUNTS_AGREE = 1.25   # larger / smaller at most this much
AMOUNTS_CLASH = 2.0    # larger / smaller beyond this is a different size of deal

DETAIL_LEVELS = ("article", "table")     # reports read from an article or a deal table, not a headline
STRONG = ("exact", "spacing", "generic_words", "fuzzy")
BASIS_LABEL = {
    "exact": "identical name",
    "spacing": "spacing or punctuation variant",
    "generic_words": "generic word added or dropped",
    "fuzzy": "spelling variant (fuzzy match)",
    "extra_words": "longer and shorter form of the name",
    "contains": "name found inside the headline",
}


@dataclass
class Report:
    """The parts of a source record that matching needs."""

    idx: int
    source: str
    day: date
    key: str
    sq: str
    core_sq: str
    usd: float | None
    stage: str
    leads: frozenset[str]
    detail: bool = True                                # False for a report read from a headline only
    article: str = ""                                  # Inc42: which weekly table the row came from
    tokens: list[str] = field(default_factory=list)   # headline words (headline sources only)


def comparable_usd(row: pd.Series, rates: pd.Series) -> float | None:
    """One USD-million figure per report: the stated dollars, or rupees at the day's reference rate."""
    usd, inr = row["amount_usd_mn"], row["amount_inr_cr"]
    if row["amount_currency"] == "USD" and pd.notna(usd):
        return float(usd)
    if pd.notna(inr):
        return inr_crore_to_usd_mn(float(inr), rate_on(rates, row["published_date"]))
    return float(usd) if pd.notna(usd) else None


def make_reports(records: pd.DataFrame, rates: pd.Series) -> list[Report]:
    reports = []
    for idx, row in records.iterrows():
        key = name_key(row["company_raw"])
        detail = row["detail_level"] in DETAIL_LEVELS
        leads = frozenset(k for k in (P.investor_key(n) for n in str(row["lead_investors"] or "").split(";")) if k) \
            if detail else frozenset()
        slug = row["url"].rstrip("/").rsplit("/", 1)[-1] if row["source"] == "vccircle" else \
            (re.search(r"/([^/]+)/articleshow/", row["url"]) or [None, ""])[1]
        reports.append(Report(
            idx=idx, source=row["source"], day=pd.Timestamp(row["published_date"]).date(), key=key, sq=squash(key),
            core_sq="".join(core_tokens(key)), usd=comparable_usd(row, rates), stage=row["stage"], leads=leads,
            detail=detail, article=row["url"] if row["source"] == "inc42" else "",
            tokens=[] if detail else slug.split("-")))
    return reports


def name_match(a_key: str, a_sq: str, a_core: str, b_key: str, b_sq: str, b_core: str) -> tuple[str, float]:
    """How two company names relate: (basis, score), or ("", 0) if they do not match."""
    if not a_sq or not b_sq:
        return "", 0.0
    if a_key == b_key:
        return "exact", 100.0
    if a_sq == b_sq:
        return "spacing", 100.0
    a_words, b_words = set(a_key.split()), set(b_key.split())
    # Generic words may only differ on one side: "Sunfox" and "Sunfox Technologies" are one
    # company, "River AI" and "River Mobility" are not.
    one_sided = a_words <= b_words or b_words <= a_words
    if a_core == b_core and len(a_core) >= 4 and one_sided:
        return "generic_words", 100.0
    a_generic, b_generic = a_words - set(core_tokens(a_key)), b_words - set(core_tokens(b_key))
    pairs = [(a_sq, b_sq)] + ([(a_core, b_core)] if a_generic <= b_generic or b_generic <= a_generic else [])
    for left, right in pairs:
        if min(len(left), len(right)) >= 5:
            score = fuzz.ratio(left, right)
            if score >= FUZZY_SCORE:
                return "fuzzy", round(score, 1)
    short, long_ = sorted((a_key.split(), b_key.split()), key=len)
    if short and long_[:len(short)] == short and len("".join(short)) >= 4:
        return "extra_words", 95.0
    shorter, longer = sorted((a_sq, b_sq), key=len)
    if len(shorter) >= 6 and longer.startswith(shorter):            # "PB Health" / "PB Healthcare"
        return "extra_words", 95.0
    return "", 0.0


def same_investor(a: str, b: str) -> bool:
    """Do two names mean one investor? ("InfoEdge Ventures" / "Info Edge Ventures", "Apis" / "Apis Partners")"""
    key_a, key_b = P.investor_key(a), P.investor_key(b)
    if not key_a or not key_b:
        return False
    flat_a, flat_b = key_a.replace(" ", ""), key_b.replace(" ", "")
    if flat_a == flat_b:
        return True
    words_a, words_b = set(key_a.split()), set(key_b.split())
    if (words_a <= words_b or words_b <= words_a) and max(len(w) for w in words_a & words_b) >= 3:
        return True
    return min(len(flat_a), len(flat_b)) >= 5 and fuzz.ratio(flat_a, flat_b) >= 88


def _amount_ratio(a: Report, b: Report) -> float | None:
    if a.usd and b.usd:
        return max(a.usd, b.usd) / min(a.usd, b.usd)
    return None


def same_round(a: Report, b: Report) -> tuple[bool, str, float]:
    """Do two detailed reports describe one round? Returns (yes/no, name basis, score)."""
    gap = abs((a.day - b.day).days)
    if gap > LONG_WINDOW:
        return False, "", 0.0
    basis, score = name_match(a.key, a.sq, a.core_sq, b.key, b.sq, b.core_sq)
    if not basis:
        return False, "", 0.0
    ratio = _amount_ratio(a, b)
    agree = ratio is not None and ratio <= AMOUNTS_AGREE
    clash = ratio is not None and ratio > AMOUNTS_CLASH
    if a.source == b.source:
        # One source listing a company twice with different amounts means two transactions
        # (tranches), and two rows of one Inc42 table are always separate deals.
        if (ratio is not None and not agree) or (a.source == "inc42" and a.article == b.article):
            return False, "", 0.0
    shared_lead = bool(a.leads & b.leads)
    same_stage = a.stage == b.stage and a.stage != "Undisclosed"
    if basis in STRONG:
        if gap <= SHORT_WINDOW:
            # A debt line and an equity round announced in the same week are two transactions.
            separate = clash and ((a.stage == "Debt") != (b.stage == "Debt"))
            return (not separate), basis, score
        return (agree or (shared_lead and same_stage)), basis, score
    # "extra_words": the shorter name could be a different company, so ask for more evidence:
    # matching amounts, a shared lead investor, or (when an amount is missing) reports two days apart at most.
    if gap <= SHORT_WINDOW and (agree or shared_lead or (ratio is None and gap <= 2)):
        return True, basis, score
    return False, "", 0.0


class _Groups:
    """Union-find over report positions."""

    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[max(ri, rj)] = min(ri, rj)


def group_detail_reports(reports: list[Report]) -> tuple[dict[int, int], dict[int, tuple[str, float]]]:
    """Cluster detailed reports. Returns ({record index: group number}, {record index: (basis, score)})."""
    reports = sorted(reports, key=lambda r: (r.day, r.idx))
    groups = _Groups(len(reports))
    joined: dict[int, tuple[str, float]] = {}
    start = 0
    for j, later in enumerate(reports):
        while (later.day - reports[start].day).days > LONG_WINDOW:
            start += 1
        for i in range(start, j):
            earlier = reports[i]
            if groups.find(i) == groups.find(j):
                continue
            ok, basis, score = same_round(earlier, later)
            if ok:
                groups.union(i, j)
                joined.setdefault(later.idx, (basis, score))
    membership = {report.idx: groups.find(pos) for pos, report in enumerate(reports)}
    return membership, joined


def _contains(tokens: list[str], name_tokens: list[str]) -> bool:
    """Is the company name inside the headline words, allowing for merged first words?"""
    if not name_tokens:
        return False
    n = len(name_tokens)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == name_tokens:
            return True
    squashed = "".join(name_tokens)
    if not tokens or len(squashed) < 5:
        return False
    # VCCircle slugs glue the first two words together: "simpleenergy-raises",
    # "thesleep-company-raises", "evmakersimple-energy-raises".
    if tokens[0].endswith(squashed):
        return True
    return any("".join(tokens[:k]).endswith(squashed) for k in range(2, n + 1))


def attach_headlines(headlines: list[Report], rounds: dict[int, list[Report]]) -> dict[int, tuple[int, str, float]]:
    """Attach each headline report to the best matching round.

    ``rounds`` maps a group number to its detailed reports. Returns
    {headline record index: (group number, basis, score)}.
    """
    spans = []
    for number, members in rounds.items():
        days = [m.day for m in members]
        usd = [m.usd for m in members if m.usd]
        spans.append((number, min(days), max(days), members, (min(usd), max(usd)) if usd else None))
    rank = {"exact": 0, "spacing": 1, "generic_words": 2, "fuzzy": 3, "contains": 4, "extra_words": 5}
    attached: dict[int, tuple[int, str, float]] = {}
    for head in headlines:
        best = None
        for number, first, last, members, usd_range in spans:
            gap = 0 if first <= head.day <= last else min(abs((head.day - first).days), abs((head.day - last).days))
            if gap > SHORT_WINDOW:
                continue
            agree = bool(head.usd and usd_range and
                         max(head.usd, usd_range[1]) / min(head.usd, usd_range[0]) <= AMOUNTS_AGREE)
            found = None
            for member in members:                         # keep the strongest evidence any report offers
                basis, score = name_match(head.key, head.sq, head.core_sq, member.key, member.sq, member.core_sq)
                if not (basis in STRONG or (basis == "extra_words" and agree)):
                    basis, score = "", 0.0
                    if _contains(head.tokens, member.key.split()) and (len(member.sq) >= 5 or agree):
                        basis, score = "contains", 100.0
                if basis and (found is None or rank[basis] < rank[found[0]]):
                    found = (basis, score)
            if found:
                candidate = (rank[found[0]], gap, 0 if agree else 1, number, found)
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
        if best:
            attached[head.idx] = (best[3], best[4][0], best[4][1])
    return attached
