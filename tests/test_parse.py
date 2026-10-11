"""Tests for the text rules. Company and investor names here are made up."""
import pytest

from funding_tracker import parse as P


# ------------------------------------------------------------------- money
@pytest.mark.parametrize("text, currency, unit, inr_crore, usd_mn", [
    ("Rs 332 crore", "INR", "crore", 332.0, None),
    ("Rs 3.61 Cr", "INR", "crore", 3.61, None),
    ("₹142 Cr", "INR", "crore", 142.0, None),
    ("INR 85 lakh", "INR", "lakh", 0.85, None),
    ("Rs 2,830 Cr", "INR", "crore", 2830.0, None),
    ("Rs 500 million", "INR", "million", 50.0, None),
    ("$35 million", "USD", "million", None, 35.0),
    ("$34.3 Mn*", "USD", "million", None, 34.3),
    ("$380K", "USD", "thousand", None, 0.38),
    ("US$1.2 Bn", "USD", "billion", None, 1200.0),
    ("USD 10 mn", "USD", "million", None, 10.0),
    ("$500,000", "USD", "units", None, 0.5),
])
def test_money_units(text, currency, unit, inr_crore, usd_mn):
    (money, _, _), = P.find_money(text)
    assert (money.currency, money.unit) == (currency, unit)
    assert money.inr_crore == inr_crore
    assert money.usd_mn == usd_mn


def test_unit_must_be_a_whole_word():
    (money, _, _), = P.find_money("raised $5000 mainly from angels")      # "m" of "mainly" is not "million"
    assert money.unit == "units" and money.usd_mn == 0.005


def test_round_amount_takes_the_figure_after_the_verb_with_its_equivalent():
    sentence = ("Lifestyle brand Acme Goods has raised Rs 332 crore (nearly $35 million) in a Series C round "
                "led by Northstar Capital.")
    primary, secondary = P.round_amount(sentence)
    assert primary.text == "Rs 332 crore" and primary.inr_crore == 332
    assert secondary.usd_mn == 35


def test_round_amount_skips_valuations():
    primary, _ = P.round_amount("Acme raises fresh capital in Series A round at Rs 110 Cr valuation")
    assert primary is None
    primary, _ = P.round_amount("Acme, last valued at $1 billion, has raised $50 million from Northstar")
    assert primary.usd_mn == 50


@pytest.mark.parametrize("slug, expected, currency", [
    ("acme-raises-rs-142-crore-led-by-northstar", 142.0, "INR"),
    ("acme-raises-8-8-mn-from-existing-investors", 8.8, "USD"),
    ("acme-raises-180-million-in-largest-round", 180.0, "USD"),
    ("acme-bags-rs-12-5-crore-in-seed-round", 12.5, "INR"),
    ("acme-secures-1-050-crore-in-series-d", 1050.0, "INR"),
    ("acme-raises-350k-in-pre-seed", 350.0, "USD"),
])
def test_slug_amount(slug, expected, currency):
    money, _ = P.slug_amount(slug)
    assert money.value == expected and money.currency == currency


def test_slug_amount_refuses_ranges_and_valuations():
    assert P.slug_amount("acme-in-talks-to-raise-rs-400-500-crore")[0] is None
    assert P.slug_amount("acme-raises-funds-at-rs-110-cr-valuation")[0] is None


# ------------------------------------------------------------------- stage
@pytest.mark.parametrize("text, stage", [
    ("Series A", "Series A"), ("series-b", "Series B"), ("Series D2", "Series D"), ("Series A2", "Series A"),
    ("pre-Series A", "Pre-Series A"), ("Pre Series B", "Pre-Series B"), ("pre-Seed", "Pre-Seed"),
    ("Pre Seed", "Pre-Seed"), ("seed funding", "Seed"), ("Series J", "Series H+"), ("pre-IPO round", "Pre-IPO"),
    ("bridge round", "Bridge"), ("venture debt", "Debt"), ("equity and debt funding", "Undisclosed"),
    ("–", "Undisclosed"), ("", "Undisclosed"), (None, "Undisclosed"),
])
def test_normalise_stage(text, stage):
    assert P.normalise_stage(text) == stage


def test_every_stage_has_a_group():
    assert set(P.STAGES) == set(P.STAGE_GROUP)


# --------------------------------------------------------------- investors
def test_split_investors_drops_fillers():
    names = P.split_investors("Northstar Capital, Blue Lake Ventures (BLV), existing investors and others")
    assert names == ["Northstar Capital", "Blue Lake Ventures"]


def test_investors_from_sentence_finds_leads_and_participants():
    sentence = ("Acme has raised $7 million in a Series A round co-led by Asha Rao, Northstar Capital and "
                "Blue Lake Ventures, with participation from Tidepool Partners.")
    leads, everyone = P.investors_from_sentence(sentence)
    assert leads == ["Asha Rao", "Northstar Capital", "Blue Lake Ventures"]
    assert everyone[-1] == "Tidepool Partners"


def test_investors_from_plain_from_clause():
    leads, everyone = P.investors_from_sentence("Acme secured $10 million from Northstar Capital and Tidepool.")
    assert leads == [] and everyone == ["Northstar Capital", "Tidepool"]


def test_investor_key_ignores_generic_words():
    assert P.investor_key("Northstar Capital Partners") == P.investor_key("Northstar")


# -------------------------------------------------------------------- city
def test_city_matcher_uses_aliases():
    find = P.build_city_matcher({"Bengaluru": "Bengaluru", "Bangalore": "Bengaluru",
                                 "Delhi NCR": "Delhi NCR", "Gurugram": "Delhi NCR"})
    assert find("Bangalore-based fintech startup Acme has raised") == "Bengaluru"
    assert find("Delhi NCR based brand Acme has raised") == "Delhi NCR"
    assert find("Acme, which is headquartered in Gurugram, has raised") == "Delhi NCR"
    assert find("Acme has raised money") == ""


# ----------------------------------------------------------- company names
@pytest.mark.parametrize("headline, company", [
    ("Acme Goods raises Rs 332 Cr in Series C round led by Northstar", "Acme Goods"),
    ("Lab-grown diamond brand Zinnia raises pre-seed funding led by Tidepool", "Zinnia"),
    ("MSME-focused digital lender NeoBridge raises Rs 85 Cr from Northstar", "NeoBridge"),
    ("Exclusive: Gramola raises fresh capital in Series A round", "Gramola"),
    ("Northstar-backed Acme secures $8.8 mn from existing investors", "Acme"),
    ("Urban Company raises $50 Mn", "Urban Company"),
    ("Game studio Lightforge Games bags $4 Mn", "Lightforge Games"),
    ("Northstar invests another $40 million in Acme Cloud", "Acme Cloud"),
    ("Northstar leads farm equipment maker Balwan Agro's Series B round", "Balwan Agro"),
])
def test_company_from_headline(headline, company):
    assert P.company_from_headline(headline) == company


@pytest.mark.parametrize("slug, company", [
    ("ayurveda-focused-d2c-startup-nat-acme-raises-rs-142-crore", "nat acme"),
    ("ev-maker-simple-acme-closes-series-c-round", "simple acme"),
    ("urban-company-raises-50-million", "urban company"),
    ("northstar-leads-farm-equipment-maker-balwan-agro-s-series-b-round", "balwan agro"),
])
def test_company_from_slug(slug, company):
    assert P.company_from_headline(P.slug_to_headline(slug), case_sensitive=False) == company


# -------------------------------------------------------------- story rules
@pytest.mark.parametrize("headline, has_amount, status", [
    ("Acme raises $180 million in largest funding round, eyes top-five spot", True, "ok"),
    ("Acme raises $15 million from Northstar, Tidepool, others", True, "ok"),
    ("Acme raises pre-seed funding led by Tidepool", False, "ok"),
    ("Acme in talks to raise Rs 400-500 crore led by Northstar", False, "excluded_not_closed"),
    ("Acme set to bag PE cheque", False, "excluded_not_closed"),
    ("Acme to kick off $15 Mn funding round led by Northstar", True, "excluded_not_closed"),
    ("Northstar Fund II secures Rs 450 Cr in first close", True, "excluded_investor_fund"),
    ("AI-focused VC firm Northstar closes maiden fund at $105 million", True, "excluded_investor_fund"),
    ("Acme raises fresh issue size to Rs 2,830 Cr in RHP", True, "excluded_not_funding"),
    ("Acme raises Rs 900 Cr from anchor investors ahead of IPO", True, "excluded_public_market"),
    ("Acme raises $20 Mn in pre-IPO round", True, "ok"),
    ("Acme, Bolt, others net funding in early-stage rounds", False, "excluded_digest"),
    ("Deals Digest: Acme, Bolt lead weekly startup funding", False, "excluded_digest"),
    ("Council raises prosecution threshold to Rs 5 Cr", True, "excluded_not_funding"),
    ("Acme gets regulator nod for payments licence", False, "excluded_not_funding"),
    ("Acme launches a new app for merchants", False, "excluded_no_funding_verb"),
])
def test_story_status(headline, has_amount, status):
    assert P.story_status(headline, has_amount) == status


# ------------------------------------------------ investor names in the wild
@pytest.mark.parametrize("sentence, leads, everyone", [
    ("Acme has raised $1.2 million led by Roots Fund along with Lake Ventures and Tidepool.",
     ["Roots Fund", "Lake Ventures", "Tidepool"], None),
    ("Acme has raised Rs 56 crore in a round led by Amal N. Parikh and Sauce.vc.", ["Amal N. Parikh", "Sauce.vc"], None),
    ("Acme raised $25 million led by Silicon Valley-based Kite Ventures and Z47.", ["Kite Ventures", "Z47"], None),
    ("Acme raised $5 million led by early-stage investment firm All Hands Capital.", ["All Hands Capital"], None),
    ("Acme raised Rs 20 crore led by Zed co-founder Nitin Rao's Rainfall.", ["Rainfall"], None),
    ("Acme has raised primary capital from a consortium of prominent investors, including Mukul Rao and SME Fund.",
     [], ["Mukul Rao", "SME Fund"]),
    ("Acme raised $1 million from angel investors in the sustainability and IT sectors.", [], []),
    ("Acme raised Rs 495 crore, led by Axis Estates LLP, whose partners include Arun Rao.", ["Axis Estates LLP"], None),
])
def test_investor_phrases(sentence, leads, everyone):
    got_leads, got_everyone = P.investors_from_sentence(sentence)
    assert got_leads == leads
    if everyone is not None:
        assert got_everyone == everyone


def test_leading_sector_word_and_generic_subjects():
    assert P.company_from_headline("Fintech Jupitar raises $15 Mn") == "Jupitar"
    assert P.company_from_headline("NBFC Bizlend raises Rs 35 Cr in Series A") == "Bizlend"
    assert P.story_status("Indian startups raise $1.75 billion in January", True) == "excluded_digest"
    assert P.story_status("Acme raises primary capital ahead of planned IPO", False) == "ok"
    assert P.story_status("Acme secures Rs 71 Cr in secondary funding round", True) == "excluded_secondary"
    assert P.story_status("Parent Co invests Rs 500 Cr in its quick commerce subsidiary Acme", True) == "excluded_intra_group"


def test_first_close_of_a_round_is_not_a_fund_raise():
    assert P.story_status("Ice cream brand Hocca raises $10 Mn in first close of Series B round", True) == "ok"
    for headline in ("Northstar raises Rs 1,000 Cr in first close of debut fund",
                     "Northstar announces first close of Rs 850 Cr for Leaders Fund III",
                     "Northstar unveils Rs 200 Cr early-stage fund for lifesciences startups",
                     "Northstar raises $129 Mn for Fund V"):
        assert P.story_status(headline, True) in ("excluded_investor_fund", "excluded_no_funding_verb"), headline


def test_first_sentence_may_carry_commas():
    sentence = "Acme, a snack brand backed by Northstar, has raised $5 million in a seed round."
    assert P.story_status(sentence, True) == "excluded_digest"                    # as a headline: a list
    assert P.story_status(sentence, True, is_sentence=True) == "ok"               # as prose: one company


def test_founder_name_in_front_of_the_company_is_dropped():
    assert P.company_from_headline("Asha Rao’s Optima Capital raises Rs 150 Cr in Series A round") == "Optima Capital"
    assert P.company_from_headline("Byju’s raises $250 Mn") == "Byju’s"


def test_company_name_in_a_story_about_a_round_still_under_way():
    assert P.company_from_headline("Acme Pay in talks to raise $50 Mn led by Northstar") == "Acme Pay"
    assert P.company_from_headline("Fintech startup Acme Pay set to raise $23 Mn in Series B") == "Acme Pay"
    assert P.company_from_headline(P.slug_to_headline("acme-pay-looks-to-raise-10-million"), case_sensitive=False) == "acme pay"


@pytest.mark.parametrize("text, names", [
    ("Capital A. The round also saw new backers", ["Capital A"]),
    ("Meta board member Charles Song, Australian high-net-worth individual Pradeep Nair", ["Charles Song", "Pradeep Nair"]),
    ("Richard Roe also joined, friends, Indian family offices and Big 4 firms", ["Richard Roe"]),
    ("Founded in 2020 by Asha Rao, 700 crore . Lake Fincorp alone, As per media reports", []),
    ("gradCapital, cKers, iSeed, 8i Ventures, Z47", ["gradCapital", "cKers", "iSeed", "8i Ventures", "Z47"]),
])
def test_investor_list_cleaning(text, names):
    assert P.split_investors(text) == names


def test_ranges_and_bare_small_numbers_are_not_round_sizes():
    primary, _ = P.round_amount("Acme has closed another $250-$270 million, taking the round's total to about $550 million")
    assert primary.usd_mn == 550
    assert P.round_amount("Acme raises Rs 400-500 crore from Northstar")[0] is None
    assert P.round_amount("Acme has raised $250 from each backer")[0] is None
    assert P.round_amount("Acme has raised $500,000 in a seed round")[0].usd_mn == 0.5
    assert P.company_from_headline("Quick service restaurant Boba Bay raises Rs 30 crore") == "Boba Bay"
    assert P.company_from_headline("Fashion search engine Shopwise raises $1 million") == "Shopwise"


@pytest.mark.parametrize("headline", [
    "Acme Drones bags around Rs 137 crore drone order from the army",
    "Acme receives Rs 7.6 crore notice from the tax department",
    "Acme gets regulator approval for a payments licence",
    "Acme secures in-principle nod for small finance bank",
    "Acme Aerospace invests $25 million for testing lab in Bengaluru",
])
def test_orders_notices_and_capex_are_not_funding(headline):
    assert P.story_status(headline, True) == "excluded_not_funding"


def test_real_rounds_survive_the_not_funding_rule():
    assert P.story_status("Acme secures $10 Mn after regulator approval", True) == "ok"
    assert P.story_status("Acme bags Rs 50 crore in Series A round led by Northstar", True) == "ok"
    assert P.story_status("Northstar invests Rs 100 crore in Acme", True) == "ok"


def test_investor_raising_a_fund_and_parent_infusions():
    assert P.story_status("Northstar Venture Partners raises $350 Mn for India fund", True) == "excluded_investor_fund"
    assert P.story_status("Optima Capital raises Rs 150 Cr in Series A round", True) == "ok"
    assert P.story_status("Acme Retail receives Rs 3,249 Cr from Singapore parent", True) == "excluded_intra_group"
    assert P.company_from_headline("IPO-bound Acme Ads raises $350 million") == "Acme Ads"


def test_schemes_grants_and_investor_subjects():
    assert P.story_status("Minister announces a Rs 1,000 crore funding scheme for space startups", True) != "ok"
    assert P.story_status("Climate tech startup gets Rs 75 lakh grant from a shipyard", True) == "excluded_not_funding"
    assert P.story_status("Impact investor Asha Ventures raises $10 million", True) == "excluded_investor_fund"
    assert P.story_status("Northquad raises Rs 550 crore for third India-focussed healthcare fund", True) == "excluded_investor_fund"
    assert P.company_from_headline("EV-maker Simple Motors raises Rs 250 crore") == "Simple Motors"
    assert P.company_from_headline("Battery recycler BatZ Energies raises Rs 105 crore") == "BatZ Energies"


@pytest.mark.parametrize("headline, status", [
    ("Emergent Labs crosses $100 million in ARR as demand for vibe coding picks up", "excluded_not_funding"),
    ("Acme Pay picks up majority stake in Pop for $30 million", "excluded_not_funding"),
    ("Acme Express gets Rs 1.3 crore over input tax credit", "excluded_not_funding"),
    ("Acme receives Rs 1,500 Cr tax demand for 2023-24", "excluded_not_funding"),
    ("New alliance on deep tech gets $1 billion commitment from investors", "excluded_not_funding"),
    ("Acme closes Bolt deal at $200 Mn", "excluded_not_funding"),
    ("Northquad raises Rs 550 crore, eyes Rs 1,700 crore fund", "excluded_investor_fund"),
    ("Valuequest raises Rs 1,500 crore from domestic LPs", "excluded_investor_fund"),
    ("Premji Invest in talks to lead Rs 200-300 crore funding round in Acme", "excluded_not_closed"),
    ("Acme raises Rs 50 crore credit line from Northstar", "ok"),
])
def test_more_story_rules(headline, status):
    has_amount = P.round_amount(headline)[0] is not None          # as the pipeline computes it
    assert P.story_status(headline, has_amount) == status
