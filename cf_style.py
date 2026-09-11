"""Climate Focus visual style — palette, Plotly template, CSS injection for Streamlit.

Palette lifted from the pptx theme "CF purple/red/blue report":
    dk1 #333333   lt1 #FFFFFF   dk2 #8451A7
    accent1 #662691  accent2 #264DA3  accent3 #459EDE  accent4 #4FB5E8
    accent5 #B8212E  accent6 #ED1C2E
    hlink   #034EA1  folHlink #6F2C90
    fonts: headings = Avenir Black, body = Avenir
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# Palette
INK        = "#333333"
PAPER      = "#FFFFFF"
MUTED_BG   = "#F4F1F8"
BLUE_DK    = "#264DA3"
BLUE_MID   = "#459EDE"
BLUE_LT    = "#4FB5E8"
PURPLE_DK  = "#662691"
PURPLE_MID = "#8451A7"
RED_DK     = "#B8212E"
RED_BRIGHT = "#ED1C2E"
LINK       = "#034EA1"

# Ordered categorical sequence — cool blues first, then purples, then warm reds for
# accent. Keep long enough for ~10 categories before repeating.
COLORWAY = [
    BLUE_DK, PURPLE_DK, BLUE_MID, PURPLE_MID, BLUE_LT,
    RED_DK, "#7BAEE8", "#A57BC6", RED_BRIGHT, "#5B7BC9",
]

FONT_STACK = "Avenir, \"Avenir Next\", \"Nunito Sans\", \"Helvetica Neue\", Arial, sans-serif"
HEADING_STACK = "\"Avenir Black\", " + FONT_STACK


def register_plotly_template() -> None:
    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        colorway=COLORWAY,
        font=dict(family=FONT_STACK, color=INK, size=13),
        title=dict(font=dict(family=HEADING_STACK, color=INK, size=16)),
        plot_bgcolor=PAPER,
        paper_bgcolor=PAPER,
        xaxis=dict(
            gridcolor="#E6E1EE",
            linecolor="#BFB8CE",
            zerolinecolor="#BFB8CE",
            title=dict(font=dict(color=INK)),
            tickfont=dict(color=INK),
        ),
        yaxis=dict(
            gridcolor="#E6E1EE",
            linecolor="#BFB8CE",
            zerolinecolor="#BFB8CE",
            title=dict(font=dict(color=INK)),
            tickfont=dict(color=INK),
        ),
        legend=dict(bgcolor=PAPER, bordercolor="#BFB8CE", borderwidth=0),
    )
    pio.templates["cf"] = tpl
    pio.templates.default = "cf"


_CSS = f"""
<style>
/* Ensure the whole app uses the CF font stack. Streamlit does not accept a custom
   font name via config.toml — only sans/serif/mono — so we override via CSS. */
html, body, .stMarkdown, .stButton>button, .stTextInput input,
.stSelectbox div, .stMultiSelect div, .stDataFrame, .stTable, p, span, div, label {{
    font-family: {FONT_STACK};
    color: {INK};
}}

/* Streamlit's icon glyphs (dropdown carets, expander arrows, dataframe sort arrows,
   info tooltip, etc.) are rendered as ligatures inside spans that use the Material
   Symbols web font. Preserve that font so the glyphs render instead of leaking the
   ligature TEXT ("arrow_drop_down", "keyboard_arrow_right"). */
.material-icons, .material-symbols-rounded, .material-symbols-outlined,
.material-symbols-sharp,
span[data-testid="stIconMaterial"],
[class*="Icon"] > span, [class*="icon"] > span,
[class*="stExpanderToggleIcon"], [class*="stExpanderIcon"] {{
    font-family: "Material Symbols Rounded", "Material Symbols Outlined",
                 "Material Icons", "Material Symbols Sharp" !important;
    font-feature-settings: "liga";
    font-variation-settings: "FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24;
}}

h1, h2, h3, h4, h5, h6 {{
    font-family: {HEADING_STACK} !important;
    color: {BLUE_DK};
    font-weight: 800;
    letter-spacing: -0.01em;
}}
h1 {{ color: {BLUE_DK}; }}
h2 {{ color: {PURPLE_DK}; }}
h3 {{ color: {BLUE_DK}; }}

/* Primary button — CF dark blue */
.stButton>button[kind="primary"] {{
    background-color: {BLUE_DK};
    border-color: {BLUE_DK};
    color: {PAPER};
}}
.stButton>button[kind="primary"]:hover {{
    background-color: {PURPLE_DK};
    border-color: {PURPLE_DK};
}}

/* Metric tiles — subtle purple tint and a thin blue rule */
[data-testid="stMetric"] {{
    background: {MUTED_BG};
    border-left: 4px solid {BLUE_DK};
    padding: 0.75rem 1rem;
    border-radius: 6px;
}}
[data-testid="stMetricLabel"] {{ color: {PURPLE_DK}; }}
[data-testid="stMetricValue"] {{ color: {BLUE_DK}; font-family: {HEADING_STACK}; }}

/* Section dividers a bit softer */
hr {{ border-top: 1px solid #E6E1EE !important; }}

/* Links */
a, a:visited {{ color: {LINK}; }}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def apply_all() -> None:
    """Call once, near the top of the Streamlit script (after st.set_page_config)."""
    register_plotly_template()
    inject_css()
