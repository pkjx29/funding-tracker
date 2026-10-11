"""Turn candidates into source records: one row per report of a funding round.

A source record keeps only facts (who raised how much, from whom, when) and the
link they came from. No article text is stored.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from datetime import date, datetime
from functools import lru_cache

import pandas as pd
from bs4 import BeautifulSoup

from . import parse as P
from .config import REFERENCE, START_DATE
from .http import Fetcher, RobotsDisallowed
from .sources import Candidate

log = logging.getLogger(__name__)

RECORD_COLUMNS = [
    "record_id", "source", "detail_level", "url", "published_date", "company_raw",
    "amount_text", "amount_currency", "amount_inr_cr", "amount_usd_mn",
    "stage_raw", "stage", "includes_debt", "lead_investors", "investors",
    "sector_raw", "subsector_raw", "business_model", "city", "status", "note",
]
TOTAL_COLUMNS = ["source", "url", "published_date", "period_start", "period_end",
                 "deals_reported", "amount_usd_mn_reported"]


# ------------------------------------------------------------ reference data
@lru_cache(maxsize=1)
def _city_aliases() -> dict[str, str]:
    with open(REFERENCE / "cities.csv", newline="", encoding="utf-8") as handle:
        return {row["alias"]: row["city"] for row in csv.DictReader(handle)}


@lru_cache(maxsize=1)
def city_finder():
    return P.build_city_matcher(_city_aliases())


@lru_cache(maxsize=1)
def _city_patterns() -> tuple[re.Pattern[str], str]:
    """("the <City>-based ... company" pattern, the alternation of every city spelling)."""
    names = "|".join(re.escape(alias) for alias in sorted(_city_aliases(), key=len, reverse=True))
    # "the Pune-based company/cafe/lender ..." points back at the article's subject, unless it names an investor.
    definite = re.compile(rf"\bthe\s+({names})\s*-?\s*(?:based|headquartered)\s+(?!(?:[\w-]+\s+){{0,3}}{_INVESTOR_WORD})"
                          r"[a-z]", re.IGNORECASE)
    return definite, names


def _blank(source: str, level: str, url: str, day: date) -> dict:
    record = dict.fromkeys(RECORD_COLUMNS, "")
    record.update(source=source, detail_level=level, url=url, published_date=day.isoformat(),
                  stage="Undisclosed", includes_debt=False, status="ok")
    return record


def _set_amount(record: dict, primary: P.Money | None, secondary: P.Money | None = None) -> None:
    if primary is None:
        return
    record["amount_text"] = primary.text
    record["amount_currency"] = primary.currency
    for money in (primary, secondary):
        if money is None:
            continue
        if money.currency == "INR":
            record["amount_inr_cr"] = money.inr_crore
        elif money.usd_mn is not None:
            record["amount_usd_mn"] = money.usd_mn


def _parse_day(text: str) -> date | None:
    text = (text or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):          # ISO dates must not be read day-first
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    try:
        stamp = pd.to_datetime(text, dayfirst=True)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(stamp) else stamp.date()


# ---------------------------------------------------------------------- Inc42
def inc42_published(html: str) -> date | None:
    match = re.search(r'property="article:published_time"\s+content="(\d{4}-\d{2}-\d{2})', html)
    return date.fromisoformat(match.group(1)) if match else None


INC42_HEADER = ["date", "name", "sector", "subsector", "business model", "funding round size",
                "funding round type", "investors", "lead investor"]
INC42_HEADER_ALIASES = {"company": "name", "startup": "name", "round size": "funding round size",
                        "funding amount": "funding round size", "round type": "funding round type",
                        "funding stage": "funding round type", "lead investors": "lead investor"}


def _inc42_footnotes(rows: list[list[str]]) -> dict[str, str]:
    """The legend under the table: {"**": "Mix of equity and debt", ...}."""
    legend = " ".join(" ".join(cells) for cells in rows if len(cells) < 6)
    notes = {}
    for marker, meaning in re.findall(r"(\*{1,3})\s*([^*]+)", legend):
        meaning = re.split(r"\bSource\s*:|\bNote\s*:", meaning)[0].strip(" .")
        if meaning:
            notes.setdefault(marker, meaning)
    return notes


def inc42_records(html: str, url: str, fallback_day: date) -> list[dict]:
    """One record per row of the weekly deal table."""
    soup = BeautifulSoup(html, "lxml")
    published = inc42_published(html) or fallback_day
    records = []
    for table in soup.find_all("table"):
        rows = [[cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
        if not rows:
            continue
        header = [INC42_HEADER_ALIASES.get(h.lower().strip(), h.lower().strip()) for h in rows[0]]
        if "name" in header:
            data = rows[1:]
        elif len(rows[0]) >= 8 and _parse_day(rows[0][0]):       # a few tables have no header row
            header, data = INC42_HEADER[:len(rows[0])], rows
        else:
            continue
        footnotes = _inc42_footnotes(data)
        number = 0
        for cells in data:
            if len(cells) < 6:                                   # legend / source line under the table
                continue
            row = dict(zip(header, cells))
            name = row.get("name", "")
            size = row.get("funding round size", "")
            marker = (re.search(r"\*+", size) or re.search(r"\*+", name) or [""])[0]
            name = name.replace("*", "").strip()
            if not name:
                continue
            number += 1
            day = _parse_day(row.get("date", "")) or published
            note = []
            # A deal cannot be dated after the article that lists it, and one
            # dated long before it is a typo in the table.
            if (day - published).days > 2 or (published - day).days > 45:
                day = published
                note.append("row date replaced by article date")
            record = _blank("inc42", "table", url, day)
            record["record_id"] = f"INC-{published:%Y%m%d}-{number:02d}"
            record["company_raw"] = name
            found = P.find_money(size)
            _set_amount(record, found[0][0] if found else None)
            record["stage_raw"] = row.get("funding round type", "").strip()
            record["stage"] = P.normalise_stage(record["stage_raw"])
            record["includes_debt"] = P.mentions_debt(record["stage_raw"])
            record["investors"] = "; ".join(P.split_investors(row.get("investors", "")))
            record["lead_investors"] = "; ".join(P.split_investors(row.get("lead investor", "")))
            record["sector_raw"] = row.get("sector", "").replace("*", "").strip()
            record["subsector_raw"] = row.get("subsector", "").strip()
            record["business_model"] = re.sub(r"\s*[-/,]\s*", "-", row.get("business model", "")).strip()
            if marker:
                meaning = footnotes.get(marker, "")
                note.append(f"source footnote: {meaning}" if meaning else "amount carries a footnote in the source")
                if "debt" in meaning.lower():
                    record["includes_debt"] = True
            if re.search(r"secondary", record["stage_raw"], re.IGNORECASE) and \
                    not re.search(r"primary", record["stage_raw"], re.IGNORECASE):
                record["status"] = "excluded_secondary"
            record["note"] = "; ".join(note)
            records.append(record)
    return records


def inc42_total(html: str, url: str, fallback_day: date) -> dict | None:
    """The week's own headline numbers: "raised $122.9 Mn across 25 deals"."""
    match = re.search(r'name="description"\s+content="([^"]+)"', html) or \
        re.search(r'property="og:description"\s+content="([^"]+)"', html)
    if not match:
        return None
    text = match.group(1)
    deals = re.search(r"across\s+(\d+)\s+deals", text, re.IGNORECASE)
    money = P.find_money(text)
    if not deals or not money:
        return None
    published = inc42_published(html) or fallback_day
    return {"source": "inc42", "url": url, "published_date": published.isoformat(), "period_start": "",
            "period_end": "", "deals_reported": int(deals.group(1)), "amount_usd_mn_reported": money[0][0].usd_mn}


# ------------------------------------------------------------------- Entrackr
_BOILERPLATE = re.compile(r"^(also read|read more|subscribe|follow us|disclaimer|update:|note:)", re.IGNORECASE)
_INVESTOR_WORD = (r"(?:investors?|investment|vc|fund|venture capital|private equity|pe firm|family office|"
                  r"asset manager|accelerator|incubator|conglomerate|bank)\b")


def entrackr_fields(html: str) -> dict:
    """Title, publication day, tagged company and body paragraphs of an Entrackr article."""
    soup = BeautifulSoup(html, "lxml")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    stamp = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', html)
    keywords: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "WebPage" and data.get("keywords"):
            raw = data["keywords"]
            keywords = raw if isinstance(raw, list) else re.findall(r"'([^']+)'|\"([^\"]+)\"", str(raw))
            keywords = [k if isinstance(k, str) else (k[0] or k[1]) for k in keywords]
    blocks = soup.find_all(["article", "div", "section"])
    body = max(blocks, key=lambda node: len(node.find_all("p", recursive=False)), default=None)
    paragraphs = []
    if body is not None:
        for p in body.find_all("p", recursive=False):
            text = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
            if len(text) >= 40 and not _BOILERPLATE.match(text):
                paragraphs.append(text)
    return {"title": title, "published": date.fromisoformat(stamp.group(1)) if stamp else None,
            "keywords": keywords, "paragraphs": paragraphs}


# Tags that label a topic, not a company.
GENERIC_TAGS = {
    "funding", "fund", "funds", "d2c", "startup", "startups", "ipo", "exclusive", "fintech", "edtech", "healthtech",
    "ev", "ai", "saas", "debt", "seed", "investment", "venture capital", "vc", "esop", "news", "india", "deeptech",
    "agritech", "ecommerce", "e-commerce", "series a", "series b", "funding round", "fundraise", "quick commerce",
}
_SENTENCE_END = re.compile(r"(?<=[a-z0-9)\]”\"])\.\s+(?=[A-Z“\"])")


def _first_sentence(paragraph: str) -> str:
    return _SENTENCE_END.split(paragraph or "", maxsplit=1)[0]


def _company_name(title: str, keywords: list[str], lead: str) -> str:
    """Company name: from the headline's grammar, else the article's company tag, else the first sentence."""
    from_title = P.company_from_headline(title)
    tagged = []
    for tag in keywords:
        hit = re.search(re.escape(tag), title, re.IGNORECASE) if tag and tag.lower() not in GENERIC_TAGS else None
        if hit:
            tagged.append(title[hit.start():hit.end()])           # spelled as in the headline
    # A tag that ends the headline-derived name is the cleaner form of it:
    # "Battery recycler BatX Energies" with the tag "BatX Energies".
    endings = [tag for tag in tagged
               if from_title.lower().endswith(" " + tag.lower())
               and any(word == word.lower() for word in from_title[:-len(tag)].split())]   # the rest is a description
    if endings:
        return max(endings, key=len)
    if from_title and len(from_title.split()) <= 4:
        return from_title
    if len(tagged) == 1 and (not from_title or tagged[0].lower() in from_title.lower()):
        return tagged[0]
    if from_title:
        return from_title
    first = _first_sentence(lead)
    named_first = re.match(r"^([A-Z0-9][^,]{1,40}),\s+(?:a|an|the)\s", first)   # "Foxtale, a skincare brand, has ..."
    return named_first.group(1).strip() if named_first else P.company_from_headline(first)


def _company_city(company: str, paragraphs: list[str]) -> str:
    """The city the article says the company is based in, or "".

    Only phrases about the company itself count. "A US-based analytics firm"
    after an investor's name describes the investor, so indefinite phrases are
    accepted only directly after the company's own name.
    """
    find = city_finder()
    definite, names = _city_patterns()
    lookup = {alias.lower(): city for alias, city in _city_aliases().items()}
    lead = paragraphs[0] if paragraphs else ""
    verb, _ = P.funding_verb(lead)
    city = find(lead[:verb.start()] if verb else lead)         # "Pune-based startup Acme has raised ..."
    if city:
        return city
    after_name = re.compile(rf"{re.escape(company)}\s*(?:\([^)]*\))?,\s+(?:a|an)\s+({names})\s*-?\s*"
                            r"(?:based|headquartered)\b", re.IGNORECASE) if company else None
    is_based = re.compile(rf"{re.escape(company)}\s+is\s+(?:based|headquartered)\s+(?:in|out of)\s+({names})\b",
                          re.IGNORECASE) if company else None
    before_name = re.compile(rf"\b({names})\s*-?\s*(?:based|headquartered)\s+{re.escape(company)}\b",
                             re.IGNORECASE) if company else None
    for text in paragraphs:
        # "the Pune-based company", "Acme, a Pune-based ...", "Acme is based in Pune", "Pune-based Acme"
        for pattern in (definite, after_name, is_based, before_name):
            match = pattern.search(text) if pattern else None
            if match:
                return lookup[re.sub(r"\s+", " ", match.group(1).lower())]
    return ""


def _article_status(title: str, lead: str, has_amount: bool) -> str:
    """Decide from the headline, then let the first sentence confirm or overrule it."""
    first = _first_sentence(lead)
    status = P.story_status(title, has_amount)
    lead_verb, _ = P.funding_verb(first)
    lead_open = bool(lead_verb and P.NOT_CLOSED.search(first[:lead_verb.end()])) or \
        bool(re.search(r"\bis (?:raising|in talks|looking|set|planning|in the process)\b", first))
    if status == "ok":
        return "excluded_not_closed" if lead_open and not P.COMPLETED.search(first) else "ok"
    if status == "excluded_no_funding_verb" and P.COMPLETED.search(first) and not lead_open:
        # Headline without a funding verb ("X kicks off new round") but the article reports a done deal.
        return P.story_status(first, has_amount, is_sentence=True)
    return status


def _article_record(source: str, record_id: str, url: str, day: date, title: str, paragraphs: list[str],
                    keywords: list[str]) -> dict:
    """A source record from an article's headline and opening paragraphs (Entrackr and ET)."""
    lead = paragraphs[0] if paragraphs else ""
    record = _blank(source, "article", url, day)
    record["record_id"] = record_id
    record["company_raw"] = _company_name(title, keywords, lead)

    primary, secondary = P.round_amount(lead)
    if primary is None:
        primary, secondary = P.round_amount(title)
    _set_amount(record, primary, secondary)

    stage_source = lead if P.normalise_stage(lead) != "Undisclosed" else title
    record["stage_raw"] = _stage_words(stage_source)
    record["stage"] = P.normalise_stage(stage_source)
    record["includes_debt"] = P.mentions_debt(lead) or P.mentions_debt(title)

    leads, everyone = P.investors_from_sentence(lead)
    if not everyone:
        leads, everyone = P.investors_from_sentence(title)
    known = {name.lower() for name in everyone}
    for text in paragraphs[1:3]:
        extra = []
        joined = re.match(r"^(.{3,220}?)\s+(?:also\s+)?(?:participated|joined|took part|invested)\b", text)
        if joined and not re.search(r"\b(said|according|plans|will|company|startup)\b", joined.group(1), re.IGNORECASE):
            extra += P.split_investors(joined.group(1))            # "A, B and C also participated in the round."
        if re.search(r"participat|also saw|round saw|investors? (?:like|such as|including)", text, re.IGNORECASE):
            extra += P.investors_from_sentence(text)[1]
        for name in extra:
            if name.lower() not in known:
                known.add(name.lower())
                everyone.append(name)
    record["lead_investors"] = "; ".join(leads)
    record["investors"] = "; ".join(everyone)
    record["city"] = _company_city(record["company_raw"], paragraphs)
    record["sector_raw"] = _description(lead, record["company_raw"])

    status = _article_status(title, lead, primary is not None)
    if status == "ok" and P.SECONDARY_ONLY.search(lead) and not P.PRIMARY_TOO.search(lead):
        status = "excluded_secondary"
    if status == "ok" and P.INTRA_GROUP.search(_first_sentence(lead)):
        status = "excluded_intra_group"
    name = record["company_raw"]
    if status == "ok" and not _is_company_name(name):
        status = "excluded_no_company"
    if P.SECONDARY_ONLY.search(lead) and P.PRIMARY_TOO.search(lead):
        record["note"] = "mix of primary and secondary"
    record["status"] = status
    return record


def _is_company_name(name: str) -> bool:
    """False for blanks, numbers, sentence fragments and generic phrases ("startups across space", "firm")."""
    words = name.lower().split()
    if not name or name.isdigit() or len(words) > 5 or name.lower() in P.NOT_A_NAME:
        return False
    if name.lower() in P.DESCRIPTOR_NOUNS or {"startups", "startup", "companies", "firms"} & set(name.split()):
        return False
    return True


def entrackr_record(html: str, url: str, fallback_day: date) -> dict:
    fields = entrackr_fields(html)
    return _article_record("entrackr", "ENT-" + url.rsplit("-", 1)[-1], url, fields["published"] or fallback_day,
                           fields["title"], fields["paragraphs"], fields["keywords"])


# ------------------------------------------------------------------------- ET
WIRE_AGENCIES = {"reuters", "bloomberg", "ap", "afp", "agencies", "nyt news service", "the new york times",
                 "financial times", "the washington post", "dow jones"}
_INDIA_HINT = re.compile(r"\b(India|Indian|India's|Bharat|Rs\.?|INR|crore|lakh)\b|₹")
_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def et_fields(html: str) -> dict | None:
    """Headline, date, tags, opening text and byline from an ET article's NewsArticle metadata."""
    for block in _LD_JSON.findall(html):
        if "NewsArticle" not in block:
            continue
        try:
            data = json.loads(block, strict=False)
        except json.JSONDecodeError:
            continue
        for item in (data if isinstance(data, list) else data.get("@graph", [data])):
            if not isinstance(item, dict) or item.get("@type") != "NewsArticle":
                continue
            stamp = re.match(r"\d{4}-\d{2}-\d{2}", str(item.get("datePublished", "")))
            body = re.sub(r"\s+", " ", str(item.get("articleBody") or ""))
            body = re.sub(r"(?<=[a-z0-9)])\.(?=[A-Z“\"])", ". ", body)          # sentences run together in the feed
            authors = item.get("author") or []
            authors = authors if isinstance(authors, list) else [authors]
            keywords = item.get("keywords") or []
            return {"title": P.strip_title(str(item.get("headline", ""))),
                    "published": date.fromisoformat(stamp.group(0)) if stamp else None,
                    "keywords": keywords if isinstance(keywords, list) else [k.strip() for k in str(keywords).split(",")],
                    "body": body,
                    "authors": [str(a.get("name", "")).strip().lower() for a in authors if isinstance(a, dict)]}
    return None


def is_about_india(text: str) -> bool:
    """Does the text mention India, a rupee amount or an Indian city?"""
    city = city_finder()(text)
    return bool(_INDIA_HINT.search(text)) or (city not in ("", "Outside India"))


def et_record(html: str, url: str, fallback_day: date) -> dict | None:
    """An ET article as a detailed report, or None if the page has no article metadata.

    ET also carries agency copy about funding rounds abroad. Those are left out
    unless the text itself ties the company to India.
    """
    fields = et_fields(html)
    if fields is None or not fields["title"]:
        return None
    record_id = "ET-" + re.search(r"/articleshow/(\d+)\.cms", url).group(1)
    sentences = [text for text in _SENTENCE_END.split(fields["body"]) if text]     # one "paragraph" per sentence
    record = _article_record("et", record_id, url, fields["published"] or fallback_day, fields["title"],
                             sentences, fields["keywords"])
    from_agency = any(author in WIRE_AGENCIES for author in fields["authors"])
    if record["status"] != "ok":
        return record
    if from_agency and not is_about_india(fields["title"] + " " + fields["body"]):
        record["status"] = "excluded_not_india"
    elif not _company_is_indian(record, fields["title"], sentences[0] if sentences else "", from_agency):
        # ET covers funding everywhere. Without a sign that the company is Indian, the story may
        # confirm a round another source reports but cannot establish one by itself.
        record["status"] = "confirm_only"
    return record


def _company_is_indian(record: dict, title: str, first_sentence: str, from_agency: bool) -> bool:
    """An Indian city for the company, a rupee amount, or (in ET's own reporting) "India" up front."""
    if record["city"] not in ("", "Outside India") or record["amount_inr_cr"] != "":
        return True
    return not from_agency and record["city"] != "Outside India" and bool(_INDIA_HINT.search(title + " " + first_sentence))


def _stage_words(text: str) -> str:
    """The words a stage was read from, e.g. "Series C" or "pre-seed"."""
    match = re.search(r"pre[\s-]*seed|pre[\s-]*series[\s-]*[a-c]\d?|pre[\s-]*ipo|series[\s-]*[a-k]\d*|\bseed\b|"
                      r"\bbridge\b|venture debt|\bdebt\b|angel (?:round|funding)", text or "", re.IGNORECASE)
    return match.group(0) if match else ""


def _description(lead: str, company: str) -> str:
    """The short description of the company in the first sentence ("lifestyle-tech brand")."""
    first = _first_sentence(lead)
    if not company or company.lower() not in first.lower():
        return ""
    position = first.lower().index(company.lower())
    if position <= 3:                                    # "Foxtale, a D2C skincare brand, has secured ..."
        after = re.match(r"[^,]*,\s+(?:a|an|the)\s+(.{3,90}?)(?:,|\s+(?:has|have|had|raised|secured)\b)",
                         first[position + len(company):])
        words = after.group(1).split() if after else []
    else:                                                # "Lifestyle-tech brand DailyObjects has raised ..."
        before = re.sub(r"^.*?(?:based|headquartered)\s+", "", first[:position], flags=re.IGNORECASE)
        words = before.strip(" ,").split()
    return " ".join(words[-8:]).lower()


def entrackr_total(html: str, url: str, fallback_day: date) -> dict | None:
    """Entrackr's weekly report: "28 Indian startups raised $113.35 million"."""
    fields = entrackr_fields(html)
    lead = fields["paragraphs"][0] if fields["paragraphs"] else ""
    match = re.search(r"(\d+)\s+(?:Indian\s+)?startups\s+(?:have\s+)?(?:raised|secured|mopped up|garnered)", lead)
    money = P.find_money(lead)
    if not match or not money:
        return None
    published = fields["published"] or fallback_day
    start, end = _week_from_title(fields["title"], published)
    return {"source": "entrackr", "url": url, "published_date": published.isoformat(),
            "period_start": start.isoformat() if start else "", "period_end": end.isoformat() if end else "",
            "deals_reported": int(match.group(1)), "amount_usd_mn_reported": money[0][0].usd_mn}


def _week_from_title(title: str, published: date) -> tuple[date | None, date | None]:
    """The week a report covers, from its title: "[Oct 5- Oct 10]", "[30 Dec- 04 Jan]", "[06-11 Jan]"."""
    bracket = re.search(r"\[([^\]]+)\]", title)
    if not bracket or "-" not in bracket.group(1).replace("–", "-"):
        return None, None
    halves = bracket.group(1).replace("–", "-").split("-", 1)
    days = [re.search(r"\d{1,2}", half) for half in halves]
    months = [re.search(r"[A-Za-z]{3,9}", half) for half in halves]
    if not all(days) or not any(months):
        return None, None
    months = [m or next(x for x in months if x) for m in months]       # "06-11 Jan": one month for both days
    try:
        start, end = (datetime.strptime(f"{d.group(0)} {m.group(0)[:3]} {published.year}", "%d %b %Y").date()
                      for d, m in zip(days, months))
    except ValueError:
        return None, None
    if (end - published).days > 30:                  # a late-December week reported in January
        end = end.replace(year=end.year - 1)
    while start > end:
        start = start.replace(year=start.year - 1)
    return start, end


# ------------------------------------------------------- headline-level (slugs)
def headline_record(candidate: Candidate) -> dict:
    """A report read from the URL slug alone, which is the headline in lower case.

    This is all there is for VCCircle. For ET it is the first pass, and the fallback
    when the article page cannot be read.
    """
    url = candidate.url
    if candidate.source == "et":
        match = re.search(r"/([^/]+)/articleshow/(\d+)\.cms", url)
        slug, record_id = match.group(1), f"ET-{match.group(2)}"
    else:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        record_id = "VCC-" + hashlib.sha1(url.encode()).hexdigest()[:10]
    headline = P.slug_to_headline(slug)
    record = _blank(candidate.source, "headline", url, candidate.day)
    record["record_id"] = record_id
    record["company_raw"] = P.company_from_headline(headline, case_sensitive=False)
    money, note = P.slug_amount(slug)
    _set_amount(record, money)
    record["stage_raw"] = _stage_words(headline)
    record["stage"] = P.normalise_stage(headline)
    record["includes_debt"] = P.mentions_debt(headline)
    led = re.search(r"\bled by (.+?)(?: others| sources| report|$)", headline)
    record["lead_investors"] = led.group(1).strip() if led else ""
    record["note"] = note
    status = P.story_status(headline, money is not None)
    name = record["company_raw"]
    if status == "ok" and (not name or name in P.NOT_A_NAME or name.isdigit()):
        status = "excluded_no_company"
    record["status"] = status
    return record


# --------------------------------------------------------------------- driver
def _page(fetcher: Fetcher, url: str) -> str | None:
    try:
        return fetcher.get(url)
    except RobotsDisallowed:
        return None


def extract(candidates: list[Candidate], fetcher: Fetcher) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse every candidate. Returns (source records, weekly totals reported by sources)."""
    records, totals = [], []
    for candidate in candidates:
        if candidate.kind == "headline":
            record = headline_record(candidate)
            if candidate.source == "et" and record["status"] == "ok":
                # The headline reads as a closed round, so the article itself is worth reading.
                # If the page is unavailable the headline-level record is kept.
                html = _page(fetcher, candidate.url)
                record = (et_record(html, candidate.url, candidate.day) if html else None) or record
            records.append(record)
            continue
        html = _page(fetcher, candidate.url)
        if html is None:
            continue
        if candidate.source == "inc42":
            records += inc42_records(html, candidate.url, candidate.day)
            total = inc42_total(html, candidate.url, candidate.day)
        elif candidate.kind == "weekly":
            total = entrackr_total(html, candidate.url, candidate.day)
        else:
            records.append(entrackr_record(html, candidate.url, candidate.day))
            total = None
        if total:
            totals.append(total)
    frame = pd.DataFrame(records, columns=RECORD_COLUMNS)
    if not frame.empty:
        outside = pd.to_datetime(frame["published_date"]).dt.date < START_DATE
        frame.loc[outside & (frame["status"] == "ok"), "status"] = "excluded_before_window"
        frame = frame.drop_duplicates("record_id", keep="last")
    return frame, pd.DataFrame(totals, columns=TOTAL_COLUMNS)
