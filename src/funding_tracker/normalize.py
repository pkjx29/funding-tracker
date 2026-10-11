"""Standardise company names and sectors so that reports can be compared."""
from __future__ import annotations

import csv
import re
from functools import lru_cache

from .config import REFERENCE
from .parse import strip_accents

_LEGAL = re.compile(r"\b(pvt|private|ltd|limited|inc|llp|llc|corp|corporation)\b\.?")
# Words that sources add or drop freely: "Sunfox" and "Sunfox Technologies" are one company.
GENERIC_WORDS = {
    "technologies", "technology", "tech", "labs", "lab", "solutions", "systems", "india", "global", "group",
    "ai", "app", "the", "company", "co", "enterprises", "industries", "innovations", "ventures", "holdings",
    "services", "health", "healthcare", "energy", "foods", "money", "pay", "finance", "financial", "capital",
    "fintech", "motors", "mobility", "robotics", "aerospace", "space", "bio", "sciences", "studio", "studios",
    "works", "digital", "online", "network", "networks", "retail", "brands", "consumer", "international",
}


def name_key(name: str) -> str:
    """Lower-case words only: "Desible.ai" -> "desible ai", "Sober & Co" -> "sober and co"."""
    key = strip_accents(name or "").lower().replace("&", " and ")
    key = re.sub(r"['’]s\b", "", key)
    key = re.sub(r"[^a-z0-9]+", " ", key)
    key = _LEGAL.sub(" ", key)
    return re.sub(r"\s+", " ", key).strip()


def squash(key: str) -> str:
    """Key with spaces removed, so "daily objects" equals "dailyobjects"."""
    return key.replace(" ", "")


def core_tokens(key: str) -> list[str]:
    """Words of a name key without the generic ones (kept if nothing else is left)."""
    tokens = key.split()
    core = [t for t in tokens if t not in GENERIC_WORDS]
    return core or tokens


# -------------------------------------------------------------------- sectors
@lru_cache(maxsize=1)
def _sector_aliases() -> dict[str, str]:
    with open(REFERENCE / "sector_aliases.csv", newline="", encoding="utf-8") as handle:
        return {row["label_in_source"].lower(): row["sector"] for row in csv.DictReader(handle)}


@lru_cache(maxsize=1)
def _sector_keywords() -> list[tuple[re.Pattern[str], str]]:
    with open(REFERENCE / "sector_keywords.csv", newline="", encoding="utf-8") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: int(row["order"]))
    return [(re.compile(row["pattern"], re.IGNORECASE), row["sector"]) for row in rows]


def sector_from_label(label: str) -> str:
    """Clean a sector label from a source table ("Enterprisetech" -> "Enterprise Tech")."""
    label = re.sub(r"\s+", " ", (label or "").replace("*", "")).strip()
    if not label or label in {"-", "–", "—"}:
        return ""
    return _sector_aliases().get(label.lower(), label)


def sector_from_description(description: str) -> str:
    """Sector implied by a short company description ("msme-focused digital lender" -> "Fintech")."""
    for pattern, sector in _sector_keywords():
        if pattern.search(description or ""):
            return sector
    return ""
