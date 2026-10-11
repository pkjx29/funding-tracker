from datetime import date

import pytest

from funding_tracker import match as M
from funding_tracker.normalize import core_tokens, name_key, squash


def names(a: str, b: str) -> tuple[str, float]:
    keys = [(name_key(n), squash(name_key(n)), "".join(core_tokens(name_key(n)))) for n in (a, b)]
    return M.name_match(*keys[0], *keys[1])


@pytest.mark.parametrize("a, b, basis", [
    ("DailyGoods", "DailyGoods", "exact"),
    ("Daily Goods", "DailyGoods", "spacing"),
    ("Desible.ai", "Desible AI", "exact"),
    ("Sunfox", "Sunfox Technologies", "generic_words"),
    ("Mosiac Wellness", "Mosaic Wellness", "fuzzy"),
    ("TrueFondry", "TrueFoundry", "fuzzy"),
    ("NOTO", "NOTO Ice Cream", "extra_words"),
    ("River AI", "River Mobility", ""),              # both carry a different second word
    ("Lumio", "Lumia", ""),                          # too short for a one-letter difference
    ("Zepto", "Zomato", ""),
])
def test_name_match(a, b, basis):
    assert names(a, b)[0] == basis


def report(name, day, source="inc42", usd=None, stage="Undisclosed", leads=(), article="", idx=0):
    key = name_key(name)
    return M.Report(idx=idx, source=source, day=date.fromisoformat(day), key=key, sq=squash(key),
                    core_sq="".join(core_tokens(key)), usd=usd, stage=stage, leads=frozenset(leads), article=article)


def test_same_name_within_ten_days_is_one_round():
    a = report("Acme", "2026-01-05", "entrackr", usd=5.0)
    b = report("Acme", "2026-01-12", "inc42", usd=5.2)
    assert M.same_round(a, b)[0]


def test_same_name_a_month_apart_needs_matching_amounts():
    early = report("Acme", "2026-01-05", "entrackr", usd=5.0)
    assert M.same_round(early, report("Acme", "2026-01-30", "inc42", usd=5.1))[0]
    assert not M.same_round(early, report("Acme", "2026-01-30", "inc42", usd=20.0))[0]
    assert not M.same_round(early, report("Acme", "2026-03-01", "inc42", usd=5.0))[0]


def test_debt_and_equity_in_one_week_stay_separate():
    equity = report("Acme", "2026-01-05", "entrackr", usd=30.0, stage="Series B")
    debt = report("Acme", "2026-01-07", "inc42", usd=4.0, stage="Debt")
    assert not M.same_round(equity, debt)[0]


def test_one_source_listing_two_amounts_means_two_rounds():
    first = report("Acme", "2026-01-05", usd=0.5, article="week-1")
    second = report("Acme", "2026-01-12", usd=2.0, article="week-2")
    assert not M.same_round(first, second)[0]
    same_table = report("Acme", "2026-01-05", usd=0.5, article="week-1")
    assert not M.same_round(first, same_table)[0]


def test_longer_and_shorter_names_need_supporting_evidence():
    short = report("Bold", "2026-01-05", "entrackr", usd=5.0)
    assert M.same_round(short, report("Bold Care", "2026-01-06", usd=5.0))[0]
    assert not M.same_round(short, report("Bold Care", "2026-01-06", usd=40.0))[0]


@pytest.mark.parametrize("a, b, same", [
    ("Info Edge Ventures", "InfoEdge Ventures", True),
    ("Apis Partners", "Apis Growth Markets Fund III", True),
    ("KOSÉ Corporation", "Kose Corporation", True),
    ("ValueQuest S.C.A.L.E. Fund", "ValueQuest Scale Fund", True),
    ("Appollo Fund", "Apollo Global Management", True),
    ("Tidepool", "Harbour Fund", False),
])
def test_same_investor(a, b, same):
    assert M.same_investor(a, b) is same


def test_headline_words_can_contain_the_company_name():
    assert M._contains("ev maker simple energy closes series c round".split(), ["simple", "energy"])
    assert M._contains("simpleenergy raises 180 mn".split(), ["simple", "energy"])        # glued by the site
    assert M._contains("thesleep company raises 50 mn".split(), ["the", "sleep", "company"])
    assert not M._contains("lambda raises 480 million in new round".split(), ["rare", "rabbit"])


def test_one_name_continuing_the_other_needs_matching_amounts():
    assert names("PB Health", "PB Healthcare")[0] == "extra_words"
    a = report("PB Health", "2025-05-08", "inc42", usd=218.0)
    assert M.same_round(a, report("PB Healthcare", "2025-05-08", "entrackr", usd=218.0))[0]
    assert not M.same_round(a, report("PB Healthcare", "2025-05-08", "entrackr", usd=20.0))[0]
