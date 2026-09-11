# GS Registry Dashboard

Local Streamlit dashboard that pulls Gold Standard Registry data from the public JSON
API and visualises project & credit metrics. Scoped to Malawi for the MVP.

## What it shows

- Project type breakdown, categorised on **methodology** (not the GS `type` tag, which
  lumps unrelated project types together — clean cooking and safe water both show up
  as "Energy Efficiency - Domestic").
- PoAs, VPAs under PoA, and Standalone VPAs per country.
- Issued credits by methodology + country.
- Retired credits by category + country.
- Top retirement beneficiaries, parsed from the free-text `note` field on retired
  credit blocks (best-effort — many notes are unstructured or anonymous).
- A filterable project browser at the bottom.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Run

```bash
./venv/bin/streamlit run dashboard.py
```

Open http://localhost:8501. Click **Refresh data** to pull the latest from
`public-api.goldstandard.org` into a local SQLite file (`gs_registry.db`, not
committed).

- **incremental** (default) — pulls only records newer than what's already in the DB.
  Fast: a few seconds.
- **full** — refetches everything. Slow (~10 min because of API rate limits). Use it
  when reseeding, or run it from the CLI: `./venv/bin/python scraper.py --mode full`.

## Layout

- `scraper.py` — paginated API scraper with retry-on-429/5xx and parent-PoA fetch.
- `dashboard.py` — Streamlit UI.
- `methodology_map.py` — methodology → project-category rules, with name-based
  fallback for VPAs whose methodology isn't exposed in the API.
- `beneficiary.py` — regex extraction of the retiring entity from the `note` field.
- `cf_style.py` — Climate Focus palette, Plotly template, Streamlit CSS overrides.
- `.streamlit/config.toml` — theme config.

## Notes

- The GS public API rate-limits aggressively; the scraper paces itself at ~0.8 req/s
  and backs off with a 30 s floor on 429 responses.
- Roughly half of VPAs have a null `methodology` field in the API. We fall back to the
  parent PoA's methodology, then to a name-based heuristic. The last ~7 % end up in
  "Unknown methodology"; extracting those would require parsing the PDD PDFs.
- Retirement `note` coverage is patchy (~50 % of blocks have a note). Where a
  beneficiary can't be parsed the credits are bucketed as "Unparsed / anonymous".
