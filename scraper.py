"""Gold Standard Registry scraper.

Pulls projects and credit blocks (issued + retired) from the public JSON API into a
local SQLite database. Scoped to Malawi (country=MW) for the MVP.

Endpoints (all GET, no auth, response is JSON array, page size 25):
    https://public-api.goldstandard.org/projects?page=N&size=25&countries=MW
    https://public-api.goldstandard.org/credits?page=N&size=25&countries=MW
        (default = issued credit blocks)
    https://public-api.goldstandard.org/credits?page=N&size=25&countries=MW&issuances=false
        (retired credit blocks — carry `note` and `retirement_use_case`)

Incremental strategy: both endpoints return newest-first by internal ID, so we page
forward and stop once we hit an ID we've already stored (unless mode="full").
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests

API_BASE = "https://public-api.goldstandard.org"
PAGE_SIZE = 25
DEFAULT_COUNTRIES = ("MW",)
DB_PATH = Path(__file__).parent / "gs_registry.db"
REQUEST_TIMEOUT = 30
POLITE_DELAY_S = 1.2   # ~0.8 req/s baseline — the API rate-limits aggressively
MAX_RETRIES = 6
MIN_BACKOFF_S = 30.0   # 429 responses can carry a bogus Retry-After: 0; enforce a floor


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id                          INTEGER PRIMARY KEY,
    sustaincert_id              INTEGER,
    name                        TEXT,
    status                      TEXT,
    type                        TEXT,           -- raw GS `type` tag (kept for reference; we categorise on methodology)
    methodology                 TEXT,           -- raw methodology string, often null on VPAs
    size                        TEXT,
    country_code                TEXT,
    country                     TEXT,
    project_developer           TEXT,
    carbon_stream               TEXT,
    gsf_standards_version       TEXT,
    crediting_period_start_date TEXT,
    crediting_period_end_date   TEXT,
    estimated_annual_credits    INTEGER,
    programme_of_activities     TEXT,           -- 'Standalone', 'PoA', 'VPA'
    poa_project_id              INTEGER,
    poa_project_sustaincert_id  INTEGER,
    poa_project_name            TEXT,
    sustaincert_url             TEXT,
    updated_at                  TEXT,
    created_at                  TEXT,
    raw_json                    TEXT,
    fetched_at                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_country  ON projects(country_code);
CREATE INDEX IF NOT EXISTS idx_projects_poa      ON projects(poa_project_id);
CREATE INDEX IF NOT EXISTS idx_projects_poa_type ON projects(programme_of_activities);

CREATE TABLE IF NOT EXISTS credit_blocks (
    id                          INTEGER PRIMARY KEY,
    project_id                  INTEGER,
    serial_number               TEXT,
    number_of_credits           INTEGER,
    batch_number                INTEGER,
    starting_credit_number      INTEGER,
    ending_credit_number        INTEGER,
    certified_date              TEXT,
    monitoring_period_start_date TEXT,
    monitoring_period_end_date   TEXT,
    status                      TEXT,           -- 'ISSUED' or 'RETIRED'
    vintage                     TEXT,
    product_name                TEXT,
    product_abbreviation        TEXT,
    retirement_use_case         TEXT,           -- only set for RETIRED
    note                        TEXT,           -- retiring entity often lives here
    country_code                TEXT,           -- copied from project for quick filtering
    updated_at                  TEXT,
    created_at                  TEXT,
    raw_json                    TEXT,
    fetched_at                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cb_project ON credit_blocks(project_id);
CREATE INDEX IF NOT EXISTS idx_cb_status  ON credit_blocks(status);
CREATE INDEX IF NOT EXISTS idx_cb_country ON credit_blocks(country_code);

CREATE TABLE IF NOT EXISTS refresh_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at              TEXT,
    mode                TEXT,          -- 'full' or 'incremental'
    countries           TEXT,
    projects_upserted   INTEGER,
    issued_upserted     INTEGER,
    retired_upserted    INTEGER,
    duration_seconds    REAL,
    ok                  INTEGER,
    error               TEXT
);
"""


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# API paging
# ---------------------------------------------------------------------------


@dataclass
class ScrapeResult:
    projects: int = 0
    issued: int = 0
    retired: int = 0


def _get_page(session: requests.Session, path: str, page: int, params: dict,
              progress: Callable[[str], None] | None = None) -> list[dict]:
    """GET one page with retry-on-429/5xx and exponential backoff."""
    q = {"page": page, "size": PAGE_SIZE, **params}
    url = f"{API_BASE}{path}"
    delay = POLITE_DELAY_S
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, params=q, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429 or (500 <= r.status_code < 600):
                retry_after = r.headers.get("Retry-After")
                header_wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 0.0
                backoff = MIN_BACKOFF_S * (2 ** (attempt - 1))
                wait = max(header_wait, backoff)
                if progress:
                    progress(f"    {r.status_code} on page {page}, waiting {wait:.0f}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(delay)
            return r.json()
        except requests.RequestException as e:
            last_exc = e
            wait = min(60, 2 ** attempt)
            if progress:
                progress(f"    request error on page {page}: {e}; waiting {wait:.1f}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
    raise last_exc if last_exc else RuntimeError(f"exhausted retries for {url}")


def _paginate(
    session: requests.Session,
    path: str,
    params: dict,
    stop_id: int | None,
    progress: Callable[[str], None] | None = None,
) -> Iterable[dict]:
    """Yield records page by page. Stop once we cross `stop_id` (already seen)."""
    page = 1
    while True:
        try:
            batch = _get_page(session, path, page, params, progress)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                # Cloudflare 403 tends to hit oversized pages / bursts — back off then break.
                if progress:
                    progress(f"  {path} page {page}: 403 (backing off, treating as end)")
                time.sleep(2.0)
                break
            raise
        if not batch:
            break
        if progress:
            progress(f"  {path} page {page}: {len(batch)} records")
        crossed = False
        for rec in batch:
            rec_id = int(rec["id"])
            if stop_id is not None and rec_id <= stop_id:
                crossed = True
                break
            yield rec
        if crossed or len(batch) < PAGE_SIZE:
            break
        page += 1


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


PROJECT_COLUMNS = (
    "id", "sustaincert_id", "name", "status", "type", "methodology", "size",
    "country_code", "country", "project_developer", "carbon_stream",
    "gsf_standards_version", "crediting_period_start_date", "crediting_period_end_date",
    "estimated_annual_credits", "programme_of_activities", "poa_project_id",
    "poa_project_sustaincert_id", "poa_project_name", "sustaincert_url",
    "updated_at", "created_at", "raw_json", "fetched_at",
)


def _upsert_project(conn: sqlite3.Connection, rec: dict, fetched_at: str) -> None:
    row = {
        "id": int(rec["id"]),
        "sustaincert_id": rec.get("sustaincert_id"),
        "name": rec.get("name"),
        "status": rec.get("status"),
        "type": rec.get("type"),
        "methodology": rec.get("methodology"),
        "size": rec.get("size"),
        "country_code": rec.get("country_code"),
        "country": rec.get("country"),
        "project_developer": rec.get("project_developer"),
        "carbon_stream": rec.get("carbon_stream"),
        "gsf_standards_version": rec.get("gsf_standards_version"),
        "crediting_period_start_date": rec.get("crediting_period_start_date"),
        "crediting_period_end_date": rec.get("crediting_period_end_date"),
        "estimated_annual_credits": rec.get("estimated_annual_credits"),
        "programme_of_activities": rec.get("programme_of_activities"),
        "poa_project_id": rec.get("poa_project_id"),
        "poa_project_sustaincert_id": rec.get("poa_project_sustaincert_id"),
        "poa_project_name": rec.get("poa_project_name"),
        "sustaincert_url": rec.get("sustaincert_url"),
        "updated_at": rec.get("updated_at"),
        "created_at": rec.get("created_at"),
        "raw_json": json.dumps(rec, separators=(",", ":")),
        "fetched_at": fetched_at,
    }
    placeholders = ",".join(f":{c}" for c in PROJECT_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO projects ({','.join(PROJECT_COLUMNS)}) VALUES ({placeholders})",
        row,
    )


CREDIT_COLUMNS = (
    "id", "project_id", "serial_number", "number_of_credits", "batch_number",
    "starting_credit_number", "ending_credit_number", "certified_date",
    "monitoring_period_start_date", "monitoring_period_end_date",
    "status", "vintage", "product_name", "product_abbreviation",
    "retirement_use_case", "note", "country_code",
    "updated_at", "created_at", "raw_json", "fetched_at",
)


def _upsert_credit(conn: sqlite3.Connection, rec: dict, fetched_at: str) -> None:
    project = rec.get("project") or {}
    product = rec.get("product") or {}
    row = {
        "id": int(rec["id"]),
        "project_id": int(project["id"]) if project.get("id") else None,
        "serial_number": rec.get("serial_number"),
        "number_of_credits": rec.get("number_of_credits"),
        "batch_number": rec.get("batch_number"),
        "starting_credit_number": rec.get("starting_credit_number"),
        "ending_credit_number": rec.get("ending_credit_number"),
        "certified_date": rec.get("certified_date"),
        "monitoring_period_start_date": rec.get("monitoring_period_start_date"),
        "monitoring_period_end_date": rec.get("monitoring_period_end_date"),
        "status": rec.get("status"),
        "vintage": rec.get("vintage"),
        "product_name": product.get("name"),
        "product_abbreviation": product.get("abbreviation"),
        "retirement_use_case": rec.get("retirement_use_case"),
        "note": rec.get("note"),
        "country_code": project.get("country_code"),
        "updated_at": rec.get("updated_at"),
        "created_at": rec.get("created_at"),
        "raw_json": json.dumps(rec, separators=(",", ":")),
        "fetched_at": fetched_at,
    }
    placeholders = ",".join(f":{c}" for c in CREDIT_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO credit_blocks ({','.join(CREDIT_COLUMNS)}) VALUES ({placeholders})",
        row,
    )
    # Also ensure the nested project record is stored, since /credits carries a fuller
    # snapshot than we sometimes get from /projects (e.g. methodology populated).
    if project.get("id"):
        _upsert_project(conn, project, fetched_at)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _max_id(conn: sqlite3.Connection, table: str, where: str = "") -> int | None:
    row = conn.execute(f"SELECT MAX(id) FROM {table} {where}").fetchone()
    return row[0]


def refresh(
    conn: sqlite3.Connection,
    mode: str = "incremental",
    countries: Iterable[str] = DEFAULT_COUNTRIES,
    progress: Callable[[str], None] | None = None,
) -> ScrapeResult:
    """Refresh the local DB. mode='full' ignores existing data; 'incremental' stops
    at the first already-seen ID on each endpoint."""
    assert mode in ("full", "incremental")
    countries_param = ",".join(countries)
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "gs-registry-dashboard/0.1"})

    # Single-country MVP; the WHERE clause below will need parameterising when we expand.
    if mode == "incremental":
        stop_project = _max_id(conn, "projects", f"WHERE country_code = '{countries_param}'")
        stop_issued = _max_id(conn, "credit_blocks", f"WHERE status='ISSUED' AND country_code = '{countries_param}'")
        stop_retired = _max_id(conn, "credit_blocks", f"WHERE status='RETIRED' AND country_code = '{countries_param}'")
    else:
        stop_project = stop_issued = stop_retired = None

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result = ScrapeResult()

    if progress: progress(f"Projects (stop_id={stop_project})…")
    for rec in _paginate(session, "/projects", {"countries": countries_param, "query": ""}, stop_project, progress):
        _upsert_project(conn, rec, fetched_at)
        result.projects += 1
    conn.commit()

    if progress: progress(f"Issued credits (stop_id={stop_issued})…")
    for rec in _paginate(session, "/credits", {"countries": countries_param, "query": ""}, stop_issued, progress):
        _upsert_credit(conn, rec, fetched_at)
        result.issued += 1
    conn.commit()

    if progress: progress(f"Retired credits (stop_id={stop_retired})…")
    for rec in _paginate(session, "/credits", {"countries": countries_param, "query": "", "issuances": "false"}, stop_retired, progress):
        _upsert_credit(conn, rec, fetched_at)
        result.retired += 1
    conn.commit()

    # Fetch any referenced parent PoA projects we don't have yet. VPAs' parent PoAs
    # are often registered in a different country, so the country filter misses them;
    # without the PoA we can't inherit its methodology on the VPA.
    missing_poa_ids = [row[0] for row in conn.execute(
        "SELECT DISTINCT poa_project_id FROM projects "
        "WHERE poa_project_id IS NOT NULL "
        "AND poa_project_id NOT IN (SELECT id FROM projects)"
    )]
    if missing_poa_ids and progress:
        progress(f"Fetching {len(missing_poa_ids)} parent PoA project(s) referenced by VPAs…")
    for poa_id in missing_poa_ids:
        try:
            r = session.get(f"{API_BASE}/projects/{poa_id}", timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                _upsert_project(conn, r.json(), fetched_at)
                result.projects += 1
            elif progress:
                progress(f"  poa {poa_id}: {r.status_code}")
            time.sleep(POLITE_DELAY_S)
        except requests.RequestException as e:
            if progress: progress(f"  poa {poa_id}: {e}")
    conn.commit()

    return result


def log_run(conn: sqlite3.Connection, mode: str, countries: Iterable[str],
            result: ScrapeResult, duration_s: float, error: str | None = None) -> None:
    conn.execute(
        """INSERT INTO refresh_log
           (ran_at, mode, countries, projects_upserted, issued_upserted, retired_upserted,
            duration_seconds, ok, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            mode,
            ",".join(countries),
            result.projects, result.issued, result.retired,
            duration_s,
            0 if error else 1,
            error,
        ),
    )
    conn.commit()


def run_refresh(mode: str = "incremental", countries: Iterable[str] = DEFAULT_COUNTRIES,
                progress: Callable[[str], None] | None = None) -> ScrapeResult:
    """Wrapper that opens the DB, times the run, and logs the result."""
    conn = open_db()
    t0 = time.time()
    error = None
    result = ScrapeResult()
    try:
        result = refresh(conn, mode=mode, countries=countries, progress=progress)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        if progress:
            progress(f"ERROR: {error}")
        raise
    finally:
        log_run(conn, mode, list(countries), result, time.time() - t0, error)
        conn.close()
    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    ap.add_argument("--countries", default="MW", help="Comma-separated ISO codes")
    args = ap.parse_args()

    def _print(msg: str) -> None:
        print(msg, flush=True)

    r = run_refresh(mode=args.mode, countries=args.countries.split(","), progress=_print)
    print(f"\nDone. projects={r.projects} issued={r.issued} retired={r.retired}")
