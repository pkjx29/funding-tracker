"""Extractor tests on small hand-written pages (names are made up)."""
from datetime import date

from funding_tracker import extract as E
from funding_tracker.sources import Candidate

INC42_PAGE = """
<html><head><meta property="article:published_time" content="2026-03-07T09:00:00+05:30" />
<meta name="description" content="Indian startups raised $98 Mn across 4 deals between March 2 and March 6" /></head>
<body><table>
<tr><th>Date</th><th>Company</th><th>Sector</th><th>Subsector</th><th>Business Model</th><th>Round Size</th>
    <th>Round Type</th><th>Investors</th><th>Lead Investor</th></tr>
<tr><td>6 Mar 2026</td><td>Acme Mart</td><td>Ecommerce</td><td>D2C</td><td>B2C</td><td>$31.6 Mn</td>
    <td>Series B</td><td>Northstar Capital, Lake Ventures, Asha Rao (Angel)</td><td>Northstar Capital</td></tr>
<tr><td>2026-03-03</td><td>Bolt Pay*</td><td>Fintech</td><td>Payments</td><td>B2B/ B2C</td><td>$8 Mn**</td>
    <td>pre-Series A</td><td>Tidepool</td><td>–</td></tr>
<tr><td>7 Sep 2026</td><td>Cargo Lane</td><td>Logistic</td><td>–</td><td>B2B</td><td>–</td><td>–</td><td>–</td><td>–</td></tr>
<tr><td>5 Mar 2026</td><td>Big Wallet</td><td>Fintech</td><td>Payments</td><td>B2C</td><td>$600 Mn</td>
    <td>Secondary Round</td><td>Fund One</td><td>Fund One</td></tr>
<tr><td colspan="9">Source: Inc42 * Mix of primary and secondary transactions ** Mix of equity and debt</td></tr>
</table></body></html>
"""


def test_inc42_table_rows_become_records():
    records = E.inc42_records(INC42_PAGE, "https://inc42.com/buzz/week/", date(2026, 3, 7))
    assert [r["company_raw"] for r in records] == ["Acme Mart", "Bolt Pay", "Cargo Lane", "Big Wallet"]
    first = records[0]
    assert first["record_id"] == "INC-20260307-01"
    assert first["published_date"] == "2026-03-06"
    assert (first["amount_usd_mn"], first["amount_currency"], first["stage"]) == (31.6, "USD", "Series B")
    assert first["investors"] == "Northstar Capital; Lake Ventures; Asha Rao"
    assert first["lead_investors"] == "Northstar Capital"


def test_inc42_footnotes_dates_and_secondaries():
    _, bolt, cargo, wallet = E.inc42_records(INC42_PAGE, "https://inc42.com/buzz/week/", date(2026, 3, 7))
    assert bolt["published_date"] == "2026-03-03"                    # ISO date is not read day-first
    assert bolt["stage"] == "Pre-Series A" and bolt["includes_debt"] is True
    assert "Mix of equity and debt" in bolt["note"]
    assert cargo["published_date"] == "2026-03-07"                   # a date after the article is a typo
    assert cargo["amount_text"] == "" and cargo["stage"] == "Undisclosed"
    assert wallet["status"] == "excluded_secondary"


def test_inc42_weekly_total():
    total = E.inc42_total(INC42_PAGE, "https://inc42.com/buzz/week/", date(2026, 3, 7))
    assert (total["deals_reported"], total["amount_usd_mn_reported"]) == (4, 98.0)


def entrackr_page(title: str, paragraphs: list[str], keywords: str = "['Acme Goods']") -> str:
    body = "".join(f"<p>{text}</p>" for text in paragraphs)
    return f"""<html><head>
    <script type="application/ld+json">{{"@type": "WebPage", "keywords": "{keywords}"}}</script>
    <script type="application/ld+json">{{"@type":"NewsArticle","datePublished":"2026-10-08T10:06:06+05:30"}}</script>
    </head><body><h1>{title}</h1><div class="story">{body}</div></body></html>"""


def test_entrackr_article_fields():
    html = entrackr_page(
        "Acme Goods raises Rs 332 Cr in Series C round led by Northstar",
        ["Delhi NCR based lifestyle brand Acme Goods has raised Rs 332 crore (nearly $35 million) in a Series C "
         "funding round led by Northstar Capital and Lake Ventures.",
         "Tidepool Partners and Asha Rao also participated in the round.",
         "The proceeds will be used to open more stores across the country over the next two years."])
    record = E.entrackr_record(html, "https://entrackr.com/news/acme-goods-raises-12638318", date(2026, 10, 9))
    assert record["record_id"] == "ENT-12638318"
    assert record["published_date"] == "2026-10-08"
    assert record["company_raw"] == "Acme Goods"
    assert (record["amount_text"], record["amount_inr_cr"], record["amount_usd_mn"]) == ("Rs 332 crore", 332.0, 35.0)
    assert record["stage"] == "Series C"
    assert record["lead_investors"] == "Northstar Capital; Lake Ventures"
    assert record["investors"] == "Northstar Capital; Lake Ventures; Tidepool Partners; Asha Rao"
    assert record["city"] == "Delhi NCR"
    assert record["sector_raw"] == "lifestyle brand"
    assert record["status"] == "ok"


def test_entrackr_status_follows_the_first_sentence():
    done = entrackr_page("Exclusive: Acme Goods kicks off new funding round",
                         ["Lifestyle brand Acme Goods has raised Rs 50 crore led by Northstar Capital.", "x" * 60])
    assert E.entrackr_record(done, "https://entrackr.com/exclusive/a-1", date(2026, 1, 5))["status"] == "ok"
    open_round = entrackr_page("Acme Goods to raise $50 Mn in new round",
                               ["Acme Goods is raising Rs 425 crore in its Series B round.", "x" * 60])
    assert E.entrackr_record(open_round, "https://entrackr.com/news/a-2", date(2026, 1, 5))["status"] == \
        "excluded_not_closed"
    fund = entrackr_page("Northstar launches $650 Mn fund for early-stage startups",
                         ["Venture capital firm Northstar has unveiled its eighth fund.", "x" * 60], "['Northstar']")
    assert E.entrackr_record(fund, "https://entrackr.com/news/a-3", date(2026, 1, 5))["status"] != "ok"


def test_entrackr_weekly_report_total():
    html = entrackr_page("Funding and acquisitions in Indian startups this week [Oct 5- Oct 10]",
                         ["This week, 28 Indian startups raised $113.35 million across 3 growth-stage deals.", "x" * 60])
    total = E.entrackr_total(html, "https://entrackr.com/report/weekly-funding-report/x-1", date(2026, 10, 10))
    assert (total["deals_reported"], total["amount_usd_mn_reported"]) == (28, 113.35)
    assert (total["period_start"], total["period_end"]) == ("2026-10-05", "2026-10-10")


def test_headline_records_from_slugs():
    et = Candidate("et", "headline", "https://economictimes.indiatimes.com/tech/startups/"
                   "ayurveda-focused-d2c-startup-nat-acme-raises-rs-142-crore-led-by-northstar-partners/"
                   "articleshow/134855727.cms", date(2026, 10, 10))
    record = E.headline_record(et)
    assert (record["record_id"], record["company_raw"], record["amount_inr_cr"]) == ("ET-134855727", "nat acme", 142.0)
    assert record["lead_investors"] == "northstar partners" and record["status"] == "ok"

    vcc = Candidate("vccircle", "headline", "https://www.vccircle.com/acme-bolt-cargo-raise-early-stage-funding",
                    date(2026, 10, 10))
    assert E.headline_record(vcc)["status"] == "excluded_digest"          # several companies in one headline

    talks = Candidate("vccircle", "headline", "https://www.vccircle.com/acme-in-talks-to-raise-50-mn",
                      date(2026, 10, 10))
    assert E.headline_record(talks)["status"] == "excluded_not_closed"


def test_city_comes_only_from_phrases_about_the_company():
    lead = "Lifestyle brand Acme Goods has raised $5 million from Northstar Capital."

    def city(second_paragraph: str) -> str:
        html = entrackr_page("Acme Goods raises $5 Mn", [lead, second_paragraph])
        return E.entrackr_record(html, "https://entrackr.com/news/a-9", date(2026, 1, 5))["city"]

    assert city("The Pune-based cafe chain plans to open ten more outlets this year.") == "Pune"
    assert city("Acme Goods, a Jaipur-based maker of quilts, was founded in 2019 by two friends.") == "Jaipur"
    assert city("Founded in 2019, Gurgaon-based Acme Goods sells quilts across the country.") == "Delhi NCR"
    # An investor's or a competitor's city is not the company's city.
    assert city("The round also saw Lake Insights, a US-based analytics firm, join the cap table.") == ""
    assert city("It competes with Mumbai-based Other Goods and several regional players.") == ""
    assert city("The Bengaluru-based venture capital firm has backed over forty startups so far.") == ""


def test_week_is_read_from_every_title_format():
    week = E._week_from_title
    assert week("... this week [Oct 5- Oct 10]", date(2026, 10, 10)) == (date(2026, 10, 5), date(2026, 10, 10))
    assert week("... this week [30 Dec- 04 Jan]", date(2025, 1, 4)) == (date(2024, 12, 30), date(2025, 1, 4))
    assert week("... this week [06-11 Jan]", date(2025, 1, 11)) == (date(2025, 1, 6), date(2025, 1, 11))
    assert week("... this week [27 Jan- Feb 01]", date(2025, 2, 1)) == (date(2025, 1, 27), date(2025, 2, 1))
    assert week("Funding report without dates", date(2025, 2, 1)) == (None, None)


def et_page(headline: str, body: str, author: str = "ETtech", keywords: str = '["Acme Cloud", "funding round"]') -> str:
    article = ('{"@context": "https://schema.org", "@type": "NewsArticle", "headline": "%s", "keywords": %s, '
               '"datePublished": "2025-02-07T24:43:52+05:30", "articleBody": "%s", '
               '"author": [{"@type": "Thing", "name": "%s"}]}' % (headline, keywords, body, author))
    return f'<html><head><script type="application/ld+json">{article}</script></head><body></body></html>'


ET_URL = "https://economictimes.indiatimes.com/tech/funding/acme-cloud-raises-131-million/articleshow/118.cms"


def test_et_article_is_read_from_its_metadata():
    html = et_page("Acme Cloud closes $131 million funding round led by Northstar Leaders Fund",
                   "Gurugram-based software firm Acme Cloud has closed a $131 million (Rs 1,120 crore) funding round "
                   "led by Northstar Leaders Fund.The round saw participation from Lake Ventures and Tidepool.")
    record = E.et_record(html, ET_URL, date(2025, 2, 9))
    assert (record["record_id"], record["detail_level"], record["published_date"]) == ("ET-118", "article", "2025-02-07")
    assert record["company_raw"] == "Acme Cloud"
    assert (record["amount_usd_mn"], record["amount_inr_cr"], record["amount_currency"]) == (131.0, 1120.0, "USD")
    assert record["lead_investors"] == "Northstar Leaders Fund"
    assert record["investors"] == "Northstar Leaders Fund; Lake Ventures; Tidepool"
    assert record["city"] == "Delhi NCR" and record["status"] == "ok"


def test_et_agency_copy_about_a_foreign_round_is_left_out():
    abroad = et_page("AI cloud startup Lambda Nine raises $480 million in new round",
                     "Lambda Nine, a cloud computing firm, has raised $480 million in a Series D round, it told Reuters.",
                     author="Reuters")
    assert E.et_record(abroad, ET_URL, date(2025, 2, 9))["status"] == "excluded_not_india"
    indian = et_page("Acme Cloud raises $40 million from Northstar",
                     "Bengaluru-based Acme Cloud has raised $40 million from Northstar, it told Reuters.", author="Reuters")
    assert E.et_record(indian, ET_URL, date(2025, 2, 9))["status"] == "ok"
    assert E.et_record("<html><body>no metadata</body></html>", ET_URL, date(2025, 2, 9)) is None


def test_et_story_without_a_sign_of_india_can_only_confirm():
    foreign = et_page("Acme Cloud raises $2 billion in seed funding",
                      "Acme Cloud, an AI lab founded by former researchers, has raised $2 billion in a seed round.")
    assert E.et_record(foreign, ET_URL, date(2025, 2, 9))["status"] == "confirm_only"
    rupees = et_page("Acme Cloud raises Rs 30 crore from Northstar",
                     "Acme Cloud has raised Rs 30-crore in a round led by Northstar.")
    record = E.et_record(rupees, ET_URL, date(2025, 2, 9))
    assert (record["status"], record["amount_inr_cr"]) == ("ok", 30.0)
