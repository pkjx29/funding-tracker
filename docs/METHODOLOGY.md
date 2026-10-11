# Methodology

How the tracker decides what a round is, which reports belong to it, and what number to show.
Every threshold named here is a constant in the code, and the file it lives in is given beside it.

## 1. What counts as a funding round

A round is a completed investment into an Indian startup that was reported on or after 1 January 2025.
One row in `rounds.csv` is one round.

A candidate story is left out, with the reason recorded in `source_records.csv`, when it is:

| Reason | Example of a headline that is left out |
| --- | --- |
| Not closed yet | "in talks to raise", "plans to raise", "set to bag", "is raising" |
| An investor raising a fund | "VC firm closes maiden fund", "first close of Fund II" |
| A public-market deal | anchor round, rights issue, QIP, IPO proceeds (a pre-IPO round is kept) |
| A pure secondary sale | shares changing hands with no money going to the company |
| A parent funding its own subsidiary | "invests Rs 500 Cr in its quick-commerce arm" |
| A round-up of several companies | "A, B, others raise early-stage funding", weekly digests |
| Not about raising money | "raises prices", "gets regulator nod", "bags an order", grants, ESOP buybacks |
| An agency story about a round abroad | Reuters or Bloomberg copy in ET with no mention of India |

These rules live in `parse.py` (`story_status`). They are applied to the headline, and for Entrackr and
ET the article's first sentence can confirm or overrule the headline: a headline without a funding verb still
counts if the first sentence reports a completed raise, and a headline that reads as done is dropped if
the first sentence says the company "is raising".

## 2. What is read from each source

| Source | Discovery | Read for each story |
| --- | --- | --- |
| Entrackr | Daily sitemaps | The article: headline, publication date, company tag, opening paragraphs |
| Inc42 | Post sitemaps, filtered to the weekly funding round-up | The deal table: date, name, sector, round size, round type, investors, lead investor |
| The Economic Times | Monthly news sitemaps, `tech/funding` and `tech/startups` | The article's metadata: headline, publication date, tags, byline and opening lines |
| VCCircle | Article sitemaps | The URL slug (article bodies are for subscribers) |

Entrackr's weekly report and each Inc42 round-up also state their own deal count. Those are stored in
`data/interim/weekly_totals.csv` and used as control totals (section 8).

ET also carries agency copy about rounds outside India. A story with an agency byline is kept only if
its text mentions India, a rupee amount or an Indian city. An ET story that shows no sign the company
is Indian (no Indian city, no rupee amount, no mention of India up front) is marked `confirm_only`: it can
confirm a round another source reports, but it cannot create one. ET pages are read only for stories whose
headline already reads as a closed round; if a page cannot be read, the story is kept at headline level.

Limits of headline-level reading (VCCircle), and how they are handled:

- A slug has no currency sign and no decimal point. "raises-8-8-mn" is read as 8.8 million dollars;
  "rs-12-5-crore" as Rs 12.5 crore; "400-500-crore" is a range and yields no amount.
- VCCircle joins the first two words of its slugs ("simpleenergy-raises"). Matching allows for that.
- Because of both, a headline never creates a round on its own (section 4).

## 3. Parsing

- **Amount.** The first amount after the funding verb that is not a valuation, revenue or fund size.
  A range ("$250-270 million") is not taken as an amount.
  If the source adds the other currency straight after it ("Rs 332 crore (nearly $35 million)") both are
  kept. Units: 1 crore = 10 million rupees, 1 lakh = 0.1 million rupees.
- **Stage.** Mapped to a fixed list: Pre-Seed, Seed, Angel, Pre-Series A, Series A, Pre-Series B,
  Series B, Pre-Series C, Series C to G, Series H+, Bridge, Pre-IPO, Debt, Undisclosed. "Series D2"
  becomes Series D. Stage groups: Early (up to Series A), Growth (Pre-Series B to C), Late (D and later,
  Pre-IPO).
- **Investors.** Names after "led by", "from", "participation from". Descriptions are stripped
  ("early-stage investment firm X" becomes "X") and fillers such as "others" are dropped.
- **City.** Only from a phrase about the company itself: "Pune-based startup X", "the Pune-based
  company", "X, a Pune-based ...". A phrase about an investor or a competitor is ignored. Spellings are
  mapped through `data/reference/cities.csv` (Bangalore to Bengaluru; Gurugram, Noida and Delhi to
  Delhi NCR; foreign headquarters to Outside India).
- **Sector.** The Inc42 table's label, with variants merged through `sector_aliases.csv`. For a round
  Inc42 did not list: the sector Inc42 gave the same company in another round, else a keyword rule on
  the article's description of the company (`sector_keywords.csv`, first match wins). `rounds.csv`
  records which basis applied.

## 4. Matching reports to rounds (`match.py`)

**Step 1: detailed reports (Entrackr, ET, Inc42).** Two reports are the same round when their names match
and their dates are close enough.

| Name relation | Example | Same round if |
| --- | --- | --- |
| Identical after removing case and punctuation | Desible.ai / Desible AI | within 10 days; or within 30 days with amounts within 25% |
| Spacing | Daily Goods / DailyGoods | same as above |
| A generic word on one side only | Sunfox / Sunfox Technologies | same as above |
| Spelling variant, rapidfuzz ratio of 90 or more, names of 5+ letters | Mosiac / Mosaic | same as above |
| One name is the start of the other | NOTO / NOTO Ice Cream | within 10 days and amounts within 25%, or a shared lead investor |

Three guards keep separate transactions separate:

- A debt line and an equity round in the same week, with amounts more than 2x apart, stay two rounds.
- If one source lists a company twice with different amounts, those are two transactions (tranches).
- Two rows of the same Inc42 table are never merged.

**Step 2: headlines (VCCircle).** Each headline is attached to at most one round whose reports fall
within 10 days of it, using the name relations above or, failing that, the round's company name
appearing inside the headline. Names shorter than five letters also need the amounts to agree.
Headlines that match nothing are written to `unmatched_headlines.csv`.

Every merged report is logged in `merge_log.csv` with the basis of the match and the rapidfuzz score.

## 5. Currency

Rupee amounts are converted at the European Central Bank reference rate for the round's announcement
date (`data/reference/usd_inr.csv`, from the Frankfurter API). Weekends and holidays use the last
published rate. Dollar amounts stated by the chosen source are used as stated.

## 6. Reconciliation

One rule per field, in [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md). Figures are never averaged. Each
report keeps its own amount in `reports_matched.csv`, and a round also carries the lowest and highest
reported amount and the spread between them.

## 7. Checks (`checks.py`)

Eighteen checks run on every build: seven for missing values, two for duplicates, one for dates, two
for outliers, two for units and currency, and four that compare sources. The README lists them.
Parameters:

| Parameter | Value | Constant |
| --- | --- | --- |
| Amount disagreement | more than 10% between highest and lowest | `AMOUNT_TOLERANCE` in `config.py` |
| INR/USD mismatch | implied rate more than 10% from the reference rate | `FX_TOLERANCE` |
| Scale mismatch | ratio within about 10% of 10, 100 or 1,000 | `scale_mismatch` |
| Stage outlier | beyond 1.5 x IQR of log10(amount); stages with at least 10 amounts | `MIN_ROUNDS_FOR_OUTLIERS` |
| Implausible amount | below $0.01 Mn or above $2,000 Mn | `MIN_AMOUNT_USD_MN`, `MAX_AMOUNT_USD_MN` |
| Possible duplicate round | same company again within 45 days | `POSSIBLE_DUPLICATE_DAYS` |
| Date disagreement | reports more than 7 days apart | `DATE_TOLERANCE_DAYS` |

## 8. Coverage control

Entrackr publishes a weekly count of funded startups. `weekly_coverage.csv` puts that count beside the
number of rounds the tracker holds for the same dates. The two will not be equal: the tracker also
holds rounds that only Inc42 listed, and Entrackr counts some deals this tracker leaves out. A week
where the tracker holds clearly fewer rounds than Entrackr counted is the signal to look for a
collection or parsing problem.

Each Inc42 round-up also states how many deals it lists. Comparing that with the rows parsed from its
table is how table-parsing errors were found during development.

## 9. Collection etiquette (`http.py`)

- A descriptive User-Agent with a link to this repository.
- robots.txt is obeyed, including `*` and `$` patterns and `Crawl-delay`.
- At least 1.5 to 2 seconds between requests to one site; 20 seconds for VCCircle, as it asks.
- Pages are cached locally and never committed. A page that answers 403 is skipped, not retried
  another way.

## 10. Known limits

- City is known only when a report states it.
- VCCircle is read at headline level and cannot supply investors or stage. ET is read from its opening
  lines only.
- Tranches of one round can appear as separate rounds (check C09 lists them).
- The parser is rule-based. Unusual phrasing can be misread; the cross-source checks are the safety net.
- Coverage starts on 1 January 2025 and depends on what the four sources chose to report.
