# Source of truth

When two sources describe the same round differently, the tracker does not keep whichever report it
happened to read first. Every field has one rule, written below. The rule decides what goes into
`data/processed/rounds.csv`. The disagreement is still flagged by the checks, with each source's own
figure listed, so a person can look at it.

| Field | Rule | Why |
| ---: | ---: | ---: |
| Company | The spelling most reports agree on, compared without case or punctuation. Ties go to the Inc42 deal table, then Entrackr, then ET. | Typos differ by source; the spelling several sources share is the safest. |
| Announced | The earliest publication date among the reports of the round. | Later reports are follow-ups. The first report is closest to the announcement. |
| Amount (as reported) | The first source that states an amount, in this order: Entrackr, Inc42, ET, VCCircle. Every other figure is kept and compared, never averaged. | Entrackr quotes the announcement currency and usually the dollar equivalent beside it. Inc42 converts everything to dollars. ET often reports a round before its final size is known. VCCircle is a headline only. |
| Amount (USD mn) | Dollars as stated when the chosen source reports in dollars. Rupee figures are converted at the ECB reference rate for the announcement date. | One published rate per day makes every conversion reproducible. |
| Amount (INR cr) | Rupees as stated when the chosen source reports in rupees, otherwise converted at the same rate. | Kept beside the dollar figure so either can be checked against the source. |
| Stage | The first source that names a stage: Inc42 table, Entrackr, ET, VCCircle. | The Inc42 table has a dedicated round-type column. The others are read from prose. |
| Lead investors / Investors | Every investor named by Inc42, Entrackr or ET, each listed once. | Sources name different subsets of a syndicate, so the union is the fullest list. |
| Sector | The Inc42 table's sector, with spelling variants merged. If Inc42 did not list the round: the sector Inc42 gave the same company in another round, else a keyword rule on the article's description of the company. Otherwise Unclassified. | One taxonomy for all rounds. The basis is recorded per round in rounds.csv. |
| City | The city an Entrackr or ET article says the company is based in. If no report of this round says so, the city from another round of the same company. Otherwise Unknown. | Only stated locations are used. Nothing is filled in from memory. |
| Sources | Every source with a matched report. A VCCircle headline can confirm a round but never creates one. | A company name read from a URL is too rough to establish a round on its own. |

## A worked example

Three reports of one round (names are made up):

| Source | Company as written | Published | Amount | Stage |
| --- | --- | --- | --- | --- |
| Entrackr (article) | Acme Goods | 8 Oct 2026 | Rs 332 crore (nearly $35 million) | Series C |
| Inc42 (deal table) | AcmeGoods | 8 Oct 2026 | $34.3 Mn | Series C |
| ET (headline) | acme goods | 9 Oct 2026 | Rs 332 crore | not stated |

What the round looks like after the rules:

| Field | Value | Rule that applied |
| --- | --- | --- |
| Company | Acme Goods | Two of the three reports spell it with a space. |
| Announced | 8 Oct 2026 | The earliest report. |
| Amount as reported | Rs 332 crore | Entrackr is first in the amount order and states one. |
| Amount (USD mn) | 332 x 10 / reference rate | Rupees converted at the ECB rate for 8 Oct 2026. |
| Stage | Series C | The Inc42 table names it. |
| Sources | entrackr, inc42, et | All three matched. |

The highest and lowest figures for this round are less than 1% apart, so it is not flagged. Had Inc42
printed $3.43 Mn, check C14 (a factor of ten) and check C15 (more than 10% apart) would both fire, and the
round would carry `C14 C15` in its `flags` column until someone resolved it.

## What the rules never do

- They never average two figures or split the difference.
- They never overwrite what a source said: each report keeps its own amount in
  `data/processed/reports_matched.csv`.
- They never fill a blank from memory. A city or a sector that no report states stays Unknown or
  Unclassified.

_This file is generated from `SOURCE_RULES` in `src/funding_tracker/reconcile.py`. Edit the rules there._
