# Data dictionary

## `data/processed/rounds.csv`

One row per funding round.

| Column | Meaning |
| --- | --- |
| `round_id` | Stable ID: `R` + date of the first report (yymmdd) + the company name without spaces. |
| `company` | Company name, in the spelling most reports agree on. |
| `announced_date` | Date of the earliest report of the round. |
| `amount_usd_mn` | Round size in USD million. Blank if no source disclosed an amount. |
| `amount_inr_cr` | Round size in INR crore. |
| `amount_as_reported` | The amount exactly as the chosen source wrote it, e.g. `Rs 332 crore`. |
| `amount_currency` | Currency the chosen source reported in: `INR` or `USD`. |
| `amount_source` | Which source supplied the amount. |
| `fx_inr_per_usd` | ECB reference rate on the announcement date, used for any conversion. |
| `stage` | Round stage from the fixed list (see METHODOLOGY.md). `Undisclosed` if no source names it. |
| `stage_group` | Early, Growth, Late, Debt, Other or Undisclosed. |
| `includes_debt` | True if any report says the round is, or includes, debt. |
| `lead_investors` | Lead investors named by a detailed source, separated by `; `. |
| `investors` | Every investor named by Inc42, Entrackr or ET, each listed once. |
| `investor_count` | Number of names in `investors`. |
| `sector` | Sector in the Inc42 taxonomy, or `Unclassified`. |
| `sector_basis` | `source table (Inc42)`, `another round of the same company` or `keyword rule on description`. |
| `subsector` | Inc42's subsector, where the round is in an Inc42 table. |
| `business_model` | Inc42's label: B2B, B2C, B2B-B2C. |
| `city` | City the company is based in, `Outside India`, or `Unknown`. |
| `city_basis` | `stated in report` or `another round of the same company`. |
| `sources` | Sources with a matched report, separated by ` \| `. |
| `source_count` | Number of distinct sources. |
| `report_count` | Number of reports matched to the round. |
| `amount_reports` | Number of sources that stated an amount. |
| `amount_low_usd_mn`, `amount_high_usd_mn` | Lowest and highest amount any report gave, in USD million. |
| `amount_spread_pct` | (highest / lowest - 1) x 100. Above 10 raises check C15. |
| `date_spread_days` | Days between the earliest and latest report. |
| `name_variants` | The different spellings found, when the reports did not agree on the name. |
| `source_urls` | One link per source. |
| `flags` | IDs of the checks this round failed, e.g. `C04 C15`. |

## `data/processed/reports_matched.csv`

One row per report that belongs to a round: `round_id`, `record_id`, `source`, `detail_level`
(`article`, `table` or `headline`), `published_date`, `company_raw` (the name as that source wrote it),
`amount_text`, `amount_currency`, `amount_inr_cr`, `amount_usd_mn` (as stated by that source, not
converted), `stage`, `lead_investors`, `url`.

## `data/interim/source_records.csv`

Every candidate story the collector read, including those left out.

| Column | Meaning |
| --- | --- |
| `record_id` | `ENT-` + Entrackr article number, `INC-` + round-up date + row number, `ET-` + article number, `VCC-` + hash of the URL. |
| `source` | `entrackr`, `inc42`, `et` or `vccircle`. |
| `detail_level` | `article`, `table` or `headline`. |
| `url`, `published_date` | Where and when the report appeared. |
| `company_raw` | Company name as read from this report. |
| `amount_text`, `amount_currency` | The primary amount as written, and its currency. |
| `amount_inr_cr`, `amount_usd_mn` | The rupee and dollar figures this report states. A report that gives both fills both. |
| `stage_raw`, `stage` | The words the stage was read from, and the standard stage. |
| `includes_debt` | True if the report mentions debt. |
| `lead_investors`, `investors` | Names separated by `; `. For headlines, the raw words after "led by". |
| `sector_raw` | Inc42: the table's sector label. Entrackr and ET: the short description of the company. |
| `subsector_raw`, `business_model` | From the Inc42 table. |
| `city` | City stated for the company, if any. |
| `status` | `ok`; `confirm_only` (may confirm a round but cannot create one); or the reason the story was left out (`excluded_not_closed`, `excluded_digest`, `excluded_not_india`, ...). |
| `note` | Parsing notes, e.g. a footnote on the amount in the source table. |

## Other files

| File | Columns |
| --- | --- |
| `data/processed/merge_log.csv` | `round_id`, `record_id`, `source`, `company_in_report`, `match_basis`, `match_score` |
| `data/processed/exceptions.csv` | `check_id`, `check`, `severity`, `round_id`, `company`, `announced_date`, `detail` |
| `data/processed/checks_summary.csv` | `check_id`, `category`, `check`, `rule`, `severity`, `rounds_checked`, `rounds_flagged`, `share_flagged` |
| `data/processed/weekly_coverage.csv` | `week_start`, `week_end`, `entrackr_deals`, `tracked_rounds`, `difference`, `entrackr_usd_mn`, `tracked_usd_mn` |
| `data/processed/unmatched_headlines.csv` | `record_id`, `source`, `published_date`, `company_raw`, `amount_text`, `stage`, `url` |
| `data/processed/not_closed_watchlist.csv` | `published_date`, `source`, `company_raw`, `amount_text`, `stage`, `url` |
| `data/interim/weekly_totals.csv` | `source`, `url`, `published_date`, `period_start`, `period_end`, `deals_reported`, `amount_usd_mn_reported` |
| `data/reference/usd_inr.csv` | `date`, `inr_per_usd` |
| `data/reference/cities.csv` | `alias`, `city` |
| `data/reference/sector_aliases.csv` | `label_in_source`, `sector` |
| `data/reference/sector_keywords.csv` | `order`, `pattern`, `sector` |
