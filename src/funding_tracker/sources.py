"""Find candidate funding stories on each source.

Discovery reads sitemaps only. What happens next depends on the source:

* Entrackr  - every candidate article is downloaded (its first sentence carries
              amount, stage, investors and often the city).
* Inc42     - the weekly round-up articles are downloaded (each has a deal table).
* ET        - the article is downloaded when its headline reads as a closed round
              (see extract.py); otherwise the URL slug is all that is used.
* VCCircle  - headline level: article bodies are subscriber-only.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator

from .http import Fetcher, RobotsDisallowed

log = logging.getLogger(__name__)

# A slug is a candidate when it contains a word that funding headlines use.
FUNDING_SLUG = re.compile(
    r"(?:^|-)(raises?|raised|secures?|secured|bags?|bagged|gets|nets|lands|closes|picks-up|mops-up|snags|"
    r"scoops|garners|receives|pockets|fund(?:ing|raise|raising|ed)?|round|invests?|infus\w*|led-by)(?:-|$)"
)

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]

URL_ENTRY = re.compile(r"<url>(.*?)</url>", re.S)
LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.S)
LASTMOD = re.compile(r"<lastmod>\s*(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Candidate:
    source: str      # entrackr | inc42 | et | vccircle
    kind: str        # article | weekly | headline
    url: str
    day: date        # publication day according to the sitemap


def _entries(xml: str) -> Iterator[tuple[str, date | None]]:
    """Yield (url, lastmod day) for every <url> in a sitemap."""
    for block in URL_ENTRY.findall(xml):
        loc = LOC.search(block)
        if not loc:
            continue
        mod = LASTMOD.search(block)
        yield loc.group(1), (date.fromisoformat(mod.group(1)) if mod else None)


def _slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].lower()


def _is_recent(day: date, until: date) -> bool:
    """Sitemaps for the last few days are still changing, so they are re-read."""
    return (until - day).days <= 3


# ------------------------------------------------------------------ Entrackr
ENTRACKR_INDEX = "https://entrackr.com/webcontent-sitemap.xml"
ENTRACKR_DAILY = re.compile(r"<loc>(https://entrackr\.com/sitemap_(\d{4}-\d{2}-\d{2})\.xml)</loc>")
ENTRACKR_WEEKLY = re.compile(r"/report/weekly-funding-report")


def entrackr_candidates(fetcher: Fetcher, since: date, until: date) -> Iterator[Candidate]:
    index = fetcher.get(ENTRACKR_INDEX, refresh=True) or ""
    for sitemap_url, stamp in ENTRACKR_DAILY.findall(index):
        day = date.fromisoformat(stamp)
        if not since <= day <= until:
            continue
        xml = fetcher.get(sitemap_url, refresh=_is_recent(day, until)) or ""
        for url, _ in _entries(xml):
            if ENTRACKR_WEEKLY.search(url):
                yield Candidate("entrackr", "weekly", url, day)
            elif FUNDING_SLUG.search(_slug(url)):
                yield Candidate("entrackr", "article", url, day)


# --------------------------------------------------------------------- Inc42
INC42_INDEX = "https://inc42.com/sitemap_index.xml"
INC42_POSTS = re.compile(r"<loc>(https://inc42\.com/post-sitemap\d*\.xml)</loc>\s*<lastmod>(\d{4}-\d{2}-\d{2})")
# "From A To B - Indian Startups Raised $X Mn This Week" (older ones start with "Funding Galore")
INC42_WEEKLY = re.compile(r"inc42\.com/[a-z0-9-]+/(?:funding-galore[^/]*|from-[^/]+-indian-startups-raised-[^/]*)/?$")


def inc42_candidates(fetcher: Fetcher, since: date, until: date) -> Iterator[Candidate]:
    index = fetcher.get(INC42_INDEX, refresh=True) or ""
    for sitemap_url, stamp in INC42_POSTS.findall(index):
        last_change = date.fromisoformat(stamp)
        if last_change < since:
            continue
        xml = fetcher.get(sitemap_url, refresh=_is_recent(last_change, until)) or ""
        for url, mod in _entries(xml):
            if mod and since <= mod <= until and INC42_WEEKLY.search(url):
                yield Candidate("inc42", "weekly", url, mod)


# ------------------------------------------------------------------------ ET
ET_INDEX = "https://economictimes.indiatimes.com/etstatic/sitemaps/et/news/sitemap-index.xml"
ET_MONTHLY = re.compile(r"<loc>(https://economictimes\.indiatimes\.com/etstatic/sitemaps/et/news/(\d{4})-([A-Za-z]+)-\d+\.xml)</loc>")
ET_SECTION = re.compile(r"economictimes\.indiatimes\.com/tech/(?:funding|startups)/([^/]+)/articleshow/\d+\.cms")


def et_candidates(fetcher: Fetcher, since: date, until: date) -> Iterator[Candidate]:
    index = fetcher.get(ET_INDEX, refresh=True) or ""
    for sitemap_url, year, month_name in ET_MONTHLY.findall(index):
        if month_name.lower() not in MONTHS:
            continue
        month_start = date(int(year), MONTHS.index(month_name.lower()) + 1, 1)
        if month_start < since.replace(day=1) or month_start > until:
            continue
        still_open = (until - month_start).days <= 35      # this month and the one before
        xml = fetcher.get(sitemap_url, refresh=still_open) or ""
        for url, mod in _entries(xml):
            match = ET_SECTION.search(url)
            if match and mod and since <= mod <= until and FUNDING_SLUG.search(match.group(1)):
                yield Candidate("et", "headline", url, mod)


# ------------------------------------------------------------------ VCCircle
VCC_SITEMAP = "https://www.vccircle.com/sitemap/article-sitemap-{n}.xml"
VCC_MAX_SITEMAPS = 66


def vccircle_candidates(fetcher: Fetcher, since: date, until: date, cached_only: bool = False) -> Iterator[Candidate]:
    """Walk the article sitemaps from newest (1) backwards until they pre-date ``since``.

    The numbered sitemaps shift as new articles arrive, so they are re-read on
    every run unless ``cached_only`` is set (used while rebuilding offline).
    """
    for n in range(1, VCC_MAX_SITEMAPS + 1):
        xml = fetcher.get(VCC_SITEMAP.format(n=n), refresh=not cached_only) or ""
        entries = [(url, mod) for url, mod in _entries(xml) if mod]
        if not entries:
            break
        for url, mod in entries:
            if since <= mod <= until and FUNDING_SLUG.search(_slug(url)):
                yield Candidate("vccircle", "headline", url, mod)
        # A sitemap can hold a few old articles that were edited recently, so the
        # stopping rule looks at the typical date rather than the oldest one.
        dates = sorted(mod for _, mod in entries)
        if dates[len(dates) // 2] < since:
            break


DISCOVERERS = {
    "entrackr": entrackr_candidates,
    "inc42": inc42_candidates,
    "et": et_candidates,
    "vccircle": vccircle_candidates,
}
NEEDS_PAGE = {("entrackr", "article"), ("entrackr", "weekly"), ("inc42", "weekly")}


def discover(source: str, fetcher: Fetcher, since: date, until: date) -> list[Candidate]:
    """All candidates for one source, without duplicates, oldest first."""
    seen: dict[str, Candidate] = {}
    try:
        for candidate in DISCOVERERS[source](fetcher, since, until):
            seen.setdefault(candidate.url, candidate)
    except RobotsDisallowed as exc:
        log.warning("%s: robots.txt disallows %s", source, exc)
    return sorted(seen.values(), key=lambda c: (c.day, c.url))


def download_pages(candidates: list[Candidate], fetcher: Fetcher) -> int:
    """Download the article pages that parsing needs. Returns how many are available."""
    available = 0
    wanted = [c for c in candidates if (c.source, c.kind) in NEEDS_PAGE]
    for i, candidate in enumerate(wanted, 1):
        try:
            if fetcher.get(candidate.url) is not None:
                available += 1
        except RobotsDisallowed:
            log.warning("robots.txt disallows %s", candidate.url)
        if i % 100 == 0:
            log.info("%s: %s of %s pages", candidate.source, i, len(wanted))
    return available


def default_window(since: date | None = None) -> tuple[date, date]:
    from .config import START_DATE
    return since or START_DATE, datetime.now().date()


if __name__ == "__main__":      # python -m funding_tracker.sources <source> [since]
    import sys

    from .config import RAW

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    name = sys.argv[1]
    start, end = default_window(date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None)
    fetcher_ = Fetcher(RAW)
    found = discover(name, fetcher_, start, end)
    log.info("%s: %s candidates (%s)", name, len(found),
             ", ".join(f"{k}={sum(c.kind == k for c in found)}" for k in ("article", "weekly", "headline")))
    got = download_pages(found, fetcher_)
    log.info("%s: %s pages available, %s requests made", name, got, fetcher_.requests_made)
