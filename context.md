# GS Registry Dashboard

- **Client:** Internal (CF tool)
- **Scope:** Local dashboard that pulls Gold Standard Registry data on demand and visualises project & credit metrics
- **Timeline:** MVP → Malawi only, expand later
- **Status:** Active — in development
- **Deadline:** —
- **Budget:** Internal time
- **Standard/methodology:** Gold Standard (all methodologies)
- **Key contacts:** Thijs

## What this project is about

Streamlit dashboard, backed by a local SQLite DB that is refreshed on demand (button) from the public Gold Standard Registry API (`public-api.goldstandard.org`). MVP is scoped to Malawi projects only.

Metrics to surface:
- Project type breakdown, derived from **methodology** (not the GS `type` tag, which lumps unrelated project types together)
- Number of PoAs and VPAs per country
- Issued credits per country + project type + methodology
- Retired credits per country + project type
- Top retirement beneficiaries (parsed from the `note` field on retired credit blocks)

## Key constraints or decisions

- **Project type from methodology code**, not `type` field. Map methodology → category (TPDDTEC → cookstoves, SDWS → safe water, AMS-I.A. → solar, etc.).
- **On-demand refresh** with a Refresh button — no cron. First run does a full backfill (~1 min for Malawi), subsequent runs are incremental based on `updated_at`.
- **API page size = 25** — endpoint 403s on larger sizes.
- **Retirement beneficiary** comes from unstructured `note` field (e.g. "Retired by X on behalf of Y") — regex extraction with a fallback bucket for unparseable notes.
- **VPA methodology** is often null in the API — inherit from parent PoA via `poa_project_id`.
- Local-only (no hosting), SQLite committable so history is preserved.

## Key files in this folder

- `context.md` — this file
- `scraper.py` — pulls from GS API into SQLite
- `dashboard.py` — Streamlit UI
- `gs_registry.db` — SQLite backing store (not committed if large)
- `methodology_map.yaml` — methodology code → project type category
