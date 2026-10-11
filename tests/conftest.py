"""A small, made-up set of source records that exercises every matching and checking rule."""
import pandas as pd
import pytest

from funding_tracker.extract import RECORD_COLUMNS


def record(record_id, source, day, company, *, level=None, text="", currency="", inr=None, usd=None,
           stage="Undisclosed", leads="", investors="", sector="", city="", status="ok", url=None):
    level = level or {"entrackr": "article", "inc42": "table"}.get(source, "headline")
    row = dict.fromkeys(RECORD_COLUMNS, "")
    row.update(record_id=record_id, source=source, detail_level=level, published_date=day, company_raw=company,
               amount_text=text, amount_currency=currency, amount_inr_cr=inr, amount_usd_mn=usd, stage=stage,
               includes_debt=False, lead_investors=leads, investors=investors or leads, sector_raw=sector, city=city,
               status=status, url=url or f"https://example.com/{record_id}")
    return row


@pytest.fixture
def rates() -> pd.Series:
    """A flat 96 rupees to the dollar keeps the arithmetic easy to follow."""
    return pd.Series(96.0, index=pd.date_range("2024-12-01", "2026-12-31", freq="D"))


@pytest.fixture
def records() -> pd.DataFrame:
    week_1, week_2 = "https://inc42.com/buzz/week-1/", "https://inc42.com/buzz/week-2/"
    rows = [
        # A. One round seen by all four sources, with the name spelled three ways.
        record("ENT-1", "entrackr", "2026-10-08", "Acme Goods", text="Rs 332 crore", currency="INR", inr=332.0,
               usd=35.0, stage="Series C", leads="Northstar Capital", city="Delhi NCR", sector="lifestyle brand"),
        record("INC-20261010-01", "inc42", "2026-10-08", "AcmeGoods", text="$34.3 Mn", currency="USD", usd=34.3,
               stage="Series C", leads="Northstar Capital Partners", investors="Northstar Capital Partners; Lake VC",
               sector="Ecommerce", url=week_2),
        record("ET-1", "et", "2026-10-09", "acme goods", text="Rs 332 crore", currency="INR", inr=332.0,
               url="https://economictimes.indiatimes.com/tech/funding/acme-goods-raises-rs-332-crore/articleshow/1.cms"),
        record("VCC-1", "vccircle", "2026-10-09", "acmegoods", text="$ 35 million", currency="USD", usd=35.0,
               url="https://www.vccircle.com/acmegoods-raises-35-mn-in-series-c"),
        # B. Lakh versus million: the two detailed sources are a factor of ~100 apart.
        record("ENT-2", "entrackr", "2026-03-03", "Bolt Pay", text="Rs 80 lakh", currency="INR", inr=0.80,
               stage="Seed", leads="Tidepool", sector="payments startup"),
        record("INC-20260307-01", "inc42", "2026-03-03", "Bolt Pay", text="$8 Mn", currency="USD", usd=8.0,
               stage="Pre-Seed", leads="Harbour Fund", sector="Fintech", url=week_1),
        # C. One source lists the company twice with different amounts: two tranches, two rounds.
        record("INC-20260307-02", "inc42", "2026-03-02", "Cargo Lane", text="$500K", currency="USD", usd=0.5,
               stage="Seed", sector="Logistics", url=week_1),
        record("INC-20260314-01", "inc42", "2026-03-10", "Cargo Lane", text="$2 Mn", currency="USD", usd=2.0,
               stage="Seed", sector="Logistics", url="https://inc42.com/buzz/week-1b/"),
        # D. A thin report: no amount, stage, investor or city.
        record("ENT-3", "entrackr", "2026-05-05", "Dash Labs", sector="ai startup"),
        # E. A typo in one source is caught by the fuzzy match; the headline settles the spelling.
        record("INC-20260620-01", "inc42", "2026-06-16", "Mosiac Wellness", text="$3 Mn", currency="USD", usd=3.0,
               stage="Series A", sector="Health Tech", url="https://inc42.com/buzz/week-3/"),
        record("ENT-4", "entrackr", "2026-06-17", "Mosaic Wellness", text="$3 million", currency="USD", usd=3.0,
               stage="Series A", city="Mumbai"),
        record("ET-2", "et", "2026-06-17", "mosaic wellness", text="$ 3 million", currency="USD", usd=3.0,
               url="https://economictimes.indiatimes.com/tech/funding/mosaic-wellness-raises-3-million/articleshow/2.cms"),
        # F. An ET article can establish a round by itself.
        record("ET-4", "et", "2026-07-01", "Yonder Labs", level="article", text="$40 million", currency="USD",
               usd=40.0, stage="Series B", leads="Harbour Fund", city="Chennai", sector="spacetech startup"),
        # A headline with no detailed report behind it, and a story that is not a closed round.
        record("ET-3", "et", "2026-06-20", "zed corp", text="$ 5 million", currency="USD", usd=5.0,
               url="https://economictimes.indiatimes.com/tech/funding/zed-corp-raises-5-million/articleshow/3.cms"),
        record("ENT-5", "entrackr", "2026-06-21", "Echo Farms", status="excluded_not_closed"),
    ]
    frame = pd.DataFrame(rows, columns=RECORD_COLUMNS)
    for column in ("amount_inr_cr", "amount_usd_mn"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame
