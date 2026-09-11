"""Streamlit dashboard for Gold Standard Registry data (MVP: Malawi only).

Run with:
    ./venv/bin/streamlit run dashboard.py
"""

from __future__ import annotations

import io
import sqlite3
import time
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from beneficiary import best_name, parse as parse_note
from cf_style import apply_all as apply_cf_style
from methodology_map import categorise, resolve_methodology, UNKNOWN_METHODOLOGY
from scraper import run_refresh

DB_PATH = Path(__file__).parent / "gs_registry.db"


# GS terminology: a "PoA" is the umbrella framework; a project activity under it is a
# "VPA under PoA"; a project activity registered on its own is a "Standalone VPA".
KIND_LABEL = {
    "POA":        "PoA",
    "VPA":        "VPA under PoA",
    "Standalone": "Standalone VPA",
    None:         "Standalone VPA",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


@st.cache_data(show_spinner=False)
def load_projects(cache_key: float) -> pd.DataFrame:
    """cache_key: pass st.session_state['data_version'] to invalidate on refresh."""
    del cache_key
    with _connect() as conn:
        df = pd.read_sql_query("SELECT * FROM projects", conn)
    if df.empty:
        return df
    # Inherit methodology from parent PoA when a VPA's methodology is null
    poa_meth = df.set_index("id")["methodology"].to_dict()
    df["methodology_resolved"] = [
        resolve_methodology(m, poa_meth.get(poa_id))
        for m, poa_id in zip(df["methodology"], df["poa_project_id"])
    ]
    df["category"] = [
        categorise(m, n) for m, n in zip(df["methodology_resolved"], df["name"])
    ]
    df["kind"] = df["programme_of_activities"].map(KIND_LABEL).fillna(KIND_LABEL[None])

    # Join actual issued / retired totals from credit_blocks. "Issued" in the GS API
    # only returns credit blocks that are still active (unretired); retirements live in
    # a separate endpoint. So the true "total ever issued" = active_issued + retired.
    with _connect() as conn:
        totals = pd.read_sql_query(
            "SELECT project_id, status, SUM(number_of_credits) AS n "
            "FROM credit_blocks GROUP BY project_id, status",
            conn,
        )
    pivoted = totals.pivot(index="project_id", columns="status", values="n").fillna(0)
    df["issued_active"] = df["id"].map(pivoted.get("ISSUED",  pd.Series(dtype=float))).fillna(0).astype(int)
    df["retired"]       = df["id"].map(pivoted.get("RETIRED", pd.Series(dtype=float))).fillna(0).astype(int)
    df["issued_total"]  = df["issued_active"] + df["retired"]
    df["pct_retired"]   = (df["retired"] / df["issued_total"]).where(df["issued_total"] > 0)
    return df


@st.cache_data(show_spinner=False)
def load_credits(cache_key: float) -> pd.DataFrame:
    del cache_key
    with _connect() as conn:
        return pd.read_sql_query("SELECT * FROM credit_blocks", conn)


@st.cache_data(show_spinner=False)
def load_refresh_log(cache_key: float) -> pd.DataFrame:
    del cache_key
    with _connect() as conn:
        return pd.read_sql_query(
            "SELECT * FROM refresh_log ORDER BY id DESC LIMIT 20", conn
        )


def db_exists() -> bool:
    return DB_PATH.exists() and DB_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# Refresh (calls the scraper in-process — works locally and on Streamlit Cloud,
# where there is no venv Python binary to subprocess into.)
# ---------------------------------------------------------------------------


def run_scraper(mode: str, countries: list[str], log_area) -> tuple[bool, str | None]:
    buf = io.StringIO()

    def progress(msg: str) -> None:
        buf.write(msg + "\n")
        log_area.code(buf.getvalue(), language="text")

    try:
        run_refresh(mode=mode, countries=countries, progress=progress)
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


st.set_page_config(page_title="GS Registry Dashboard", layout="wide")
apply_cf_style()
st.title("Gold Standard Registry — Malawi")
st.caption("Local dashboard backed by SQLite. Click **Refresh data** to pull the latest from `public-api.goldstandard.org`.")

if "data_version" not in st.session_state:
    st.session_state.data_version = time.time()

# --- Refresh controls ---
col_a, col_b, col_c = st.columns([1, 1, 3])
with col_a:
    refresh_clicked = st.button("🔄 Refresh data", type="primary", use_container_width=True)
with col_b:
    mode = st.selectbox(
        "Mode",
        ["incremental", "full"],
        index=0,
        help=(
            "**incremental** (default) — only pulls records newer than what's already "
            "in the local DB. Fast: a few seconds.\n\n"
            "**full** — refetches every project and credit block from scratch. Slow "
            "(~10 min because of API rate limits). Useful when reseeding the DB or "
            "when you suspect the incremental cursor drifted."
        ),
    )
with col_c:
    if db_exists():
        log = load_refresh_log(st.session_state.data_version)
        if not log.empty:
            last = log.iloc[0]
            ok_icon = "✅" if last.get("ok") else "❌"
            st.caption(
                f"{ok_icon} Last refresh: **{last['ran_at']} UTC** · mode=`{last['mode']}` · "
                f"projects+{last['projects_upserted']} · issued+{last['issued_upserted']} · "
                f"retired+{last['retired_upserted']} · {last['duration_seconds']:.1f}s"
            )
    else:
        st.caption("No data yet — click **Refresh data** to run the first backfill.")

if refresh_clicked:
    with st.expander("Scraper log", expanded=True):
        log_area = st.empty()
        with st.spinner("Scraping GS Registry…"):
            ok, err = run_scraper(mode, ["MW"], log_area)
        if ok:
            st.success("Refresh complete.")
        else:
            st.error(f"Scraper failed: {err}")
    st.session_state.data_version = time.time()
    st.rerun()

if not db_exists():
    st.info("Empty database. Run a refresh to populate it.")
    st.stop()

projects = load_projects(st.session_state.data_version)
credits  = load_credits(st.session_state.data_version)

if projects.empty:
    st.warning("No projects in the database yet.")
    st.stop()

# ---------------------------------------------------------------------------
# Summary tiles
# ---------------------------------------------------------------------------

kind_counts   = projects["kind"].value_counts()
issued_active = credits[credits["status"] == "ISSUED"]   # currently outstanding
retired       = credits[credits["status"] == "RETIRED"]  # all-time retired
issued        = credits                                  # total ever issued (active + retired)

total_issued  = int(issued["number_of_credits"].sum())
total_retired = int(retired["number_of_credits"].sum())
total_active  = int(issued_active["number_of_credits"].sum())

tile_cols = st.columns(6)
tile_cols[0].metric("Projects", len(projects))
tile_cols[1].metric("PoAs", int(kind_counts.get("PoA", 0)))
tile_cols[2].metric("VPAs under PoA", int(kind_counts.get("VPA under PoA", 0)))
tile_cols[3].metric("Standalone VPAs", int(kind_counts.get("Standalone VPA", 0)))
tile_cols[4].metric(
    "Credits issued (total)",
    f"{total_issued:,}",
    help=f"All credits ever issued for Malawi projects (active + retired). "
         f"{total_active:,} currently outstanding · {total_retired:,} retired.",
)
tile_cols[5].metric(
    "Credits retired",
    f"{total_retired:,}",
    help=(
        f"All-time retirements — {total_retired / total_issued * 100:.1f}% "
        f"of total ever issued."
    ) if total_issued else "",
)

st.divider()

# ---------------------------------------------------------------------------
# Project types (from methodology, not GS tag)
# ---------------------------------------------------------------------------

st.subheader("Project types (categorised on methodology, not GS `type` tag)")

cat_df = (
    projects.groupby("category").size().reset_index(name="projects")
    .sort_values("projects", ascending=False)
)
col1, col2 = st.columns([2, 1])
with col1:
    fig = px.bar(cat_df, x="projects", y="category", orientation="h",
                 text="projects", height=380)
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)
with col2:
    unknown = int((projects["category"] == UNKNOWN_METHODOLOGY).sum())
    st.markdown(f"**Coverage:** {len(projects) - unknown} of {len(projects)} projects have a known methodology "
                f"({unknown} still `Unknown methodology` — usually VPAs whose parent PoA methodology hasn't been ingested yet).")
    st.dataframe(cat_df, hide_index=True, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# PoAs & VPAs per country
# ---------------------------------------------------------------------------

st.subheader("PoAs and VPAs per country")

poa_country = (
    projects.groupby(["country", "kind"]).size().reset_index(name="projects")
)
kind_order = ["PoA", "VPA under PoA", "Standalone VPA"]
fig = px.bar(poa_country, x="country", y="projects", color="kind", barmode="group",
             height=380, text="projects", category_orders={"kind": kind_order})
fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
st.plotly_chart(fig, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Issued credits by methodology + country
# ---------------------------------------------------------------------------

st.subheader("Issued credits by methodology + country")
st.caption("Total credits ever issued for each methodology (currently outstanding + already retired).")

if issued.empty:
    st.info("No issued credits in the database yet.")
else:
    proj_view = projects.set_index("id")[["methodology_resolved", "category", "country"]]
    issued_j = issued.join(proj_view, on="project_id")
    by_meth = (
        issued_j.assign(methodology=issued_j["methodology_resolved"].fillna("Unknown methodology"))
        .groupby(["country", "methodology", "category"])["number_of_credits"].sum().reset_index()
        .sort_values("number_of_credits", ascending=False)
    )
    fig = px.bar(by_meth.head(30), x="number_of_credits", y="methodology", color="country",
                 orientation="h", height=520, text="number_of_credits", hover_data=["category"])
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Full table"):
        st.dataframe(by_meth, hide_index=True, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Retired credits by category + country
# ---------------------------------------------------------------------------

st.subheader("Retired credits by category + country")

if retired.empty:
    st.info("No retired credits in the database yet.")
else:
    proj_view = projects.set_index("id")[["category", "country"]]
    retired_j = retired.join(proj_view, on="project_id")
    by_cat = (
        retired_j.groupby(["country", "category"])["number_of_credits"].sum().reset_index()
        .sort_values("number_of_credits", ascending=False)
    )
    fig = px.bar(by_cat, x="category", y="number_of_credits", color="country", barmode="stack",
                 height=380, text="number_of_credits")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Top retirement beneficiaries
# ---------------------------------------------------------------------------

st.subheader("Top retirement beneficiaries")
st.caption("Parsed from the free-text `note` field on retired credit blocks. "
           "'(retirer only)' rows show the retiring party where no end beneficiary was named — "
           "usually a broker retiring on behalf of an unnamed client.")

if retired.empty:
    st.info("No retirements to attribute yet.")
else:
    parsed = retired.copy()
    parsed["beneficiary"] = parsed["note"].map(best_name)
    top = (
        parsed.groupby("beneficiary")["number_of_credits"].sum().reset_index()
        .sort_values("number_of_credits", ascending=False).head(30)
    )
    col1, col2 = st.columns([2, 1])
    with col1:
        fig = px.bar(top.head(20), x="number_of_credits", y="beneficiary", orientation="h",
                     height=560, text="number_of_credits")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        total = int(retired["number_of_credits"].sum())
        parsed_notes = parsed[~parsed["note"].isna() & (parsed["note"].str.strip() != "")]
        st.markdown(
            f"**Coverage:** {len(parsed_notes)} of {len(retired)} retirement blocks have a `note`. "
            f"Total retired credits: **{total:,}**."
        )
        st.dataframe(top, hide_index=True, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Project browser
# ---------------------------------------------------------------------------

st.subheader("Project browser")

kind_filter = st.multiselect(
    "Kind",
    options=[k for k in kind_order if k in projects["kind"].unique()],
    default=None,
)
cat_filter = st.multiselect(
    "Category",
    options=sorted(projects["category"].unique()),
    default=None,
)

view = projects.copy()
if kind_filter:
    view = view[view["kind"].isin(kind_filter)]
if cat_filter:
    view = view[view["category"].isin(cat_filter)]

show_cols = [
    "sustaincert_id", "name", "kind", "category", "methodology_resolved",
    "status", "country", "project_developer",
    "issued_total", "retired", "issued_active", "pct_retired",
    "estimated_annual_credits",
    "crediting_period_start_date", "crediting_period_end_date", "sustaincert_url",
]
st.dataframe(
    view[show_cols],
    hide_index=True,
    use_container_width=True,
    height=420,
    column_config={
        "issued_total":  st.column_config.NumberColumn("Issued (total)", format="%d",
            help="All credits ever issued for this project = active + retired."),
        "retired":       st.column_config.NumberColumn("Retired", format="%d"),
        "issued_active": st.column_config.NumberColumn("Issued (active)", format="%d",
            help="Issued credits still in circulation (not yet retired)."),
        "pct_retired":   st.column_config.NumberColumn("% retired", format="%.1f%%",
            help="Retired ÷ total ever issued. Blank when the project has no issuances."),
        "estimated_annual_credits": st.column_config.NumberColumn("Est. annual credits (PDD)",
            format="%d", help="Planning figure from the PDD — not actual issuances."),
        "sustaincert_url": st.column_config.LinkColumn("SustainCert link"),
    },
)
st.caption(f"{len(view)} projects shown.")
