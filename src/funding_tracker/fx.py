"""USD/INR reference rates.

Rates are the European Central Bank reference rates, served by the free
Frankfurter API. They are stored in ``data/reference/usd_inr.csv`` so that the
pipeline can be re-run offline and every conversion can be reproduced.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .config import REFERENCE, START_DATE
from .http import TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

FX_FILE = REFERENCE / "usd_inr.csv"
FX_API = "https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=INR"


def update_rates(path: Path = FX_FILE, until: date | None = None) -> pd.DataFrame:
    """Download any missing days and rewrite the CSV. Keeps the old file if the API fails."""
    until = until or date.today()
    known = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "inr_per_usd"])
    start = START_DATE - timedelta(days=7)
    if not known.empty:
        start = max(start, date.fromisoformat(known["date"].max()) - timedelta(days=7))
    try:
        response = requests.get(FX_API.format(start=start, end=until), timeout=TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        fresh = pd.DataFrame(
            [{"date": day, "inr_per_usd": quote["INR"]} for day, quote in response.json()["rates"].items()])
    except (requests.RequestException, KeyError, ValueError) as exc:
        log.warning("FX update failed, keeping the stored rates: %s", exc)
        return known
    rates = pd.concat([known, fresh]).drop_duplicates("date", keep="last").sort_values("date")
    path.parent.mkdir(parents=True, exist_ok=True)
    rates.to_csv(path, index=False)
    return rates


def load_rates(path: Path = FX_FILE) -> pd.Series:
    """INR per USD for every calendar day (weekends and holidays carry the last published rate)."""
    rates = pd.read_csv(path, parse_dates=["date"]).set_index("date")["inr_per_usd"].sort_index()
    every_day = pd.date_range(rates.index.min(), max(rates.index.max(), pd.Timestamp(date.today())), freq="D")
    return rates.reindex(every_day).ffill()


def rate_on(rates: pd.Series, day) -> float:
    """The reference rate that applied on ``day`` (clamped to the range we hold)."""
    stamp = min(max(pd.Timestamp(day), rates.index[0]), rates.index[-1])
    return float(rates.loc[stamp])


def inr_crore_to_usd_mn(inr_crore: float, rate: float) -> float:
    """1 crore = 10 million rupees, so Rs X crore = X * 10 / rate million dollars."""
    return inr_crore * 10.0 / rate
