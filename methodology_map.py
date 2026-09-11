"""Map Gold Standard methodology strings to project-type categories.

We deliberately categorise on the METHODOLOGY (as the user requested), not on the GS
`type` tag, which lumps unrelated project types together (e.g. improved cookstoves and
safe drinking water both show up as "Energy Efficiency - Domestic").

The API returns methodology as a free-text string like:
    "AMS-I.A. Electricity generation by the user"
    "Technologies and Practices to Displace Decentralized Thermal Energy Consumption v3.1"
    "Safe Drinking Water Supply v2.0"
    None      # common for VPAs; caller should inherit from parent PoA before categorising

`categorise()` returns one of a small set of canonical labels. Add/adjust patterns as
new methodologies appear in the data.
"""

from __future__ import annotations

import re

# Ordered rules: (regex, category). First match wins.
_RULES: list[tuple[re.Pattern, str]] = [
    # Cookstoves / improved thermal cooking
    (re.compile(r"TPDDTEC|Technologies and Practices to Displace Decentralized Thermal", re.I), "Clean cooking"),
    (re.compile(r"\bAMS[- ]?II\.G\b", re.I), "Clean cooking"),                    # EE in thermal apps of non-renewable biomass
    (re.compile(r"\bAMS[- ]?I\.E\b", re.I), "Clean cooking"),                     # Switch from non-renewable biomass
    (re.compile(r"improved cookstove|clean cooking|cookstove", re.I), "Clean cooking"),
    (re.compile(r"metered.*energy cooking|energy cooking device", re.I), "Clean cooking"),

    # Safe drinking water
    (re.compile(r"\bSDWS\b|Safe Drinking Water Supply", re.I), "Safe drinking water"),
    (re.compile(r"\bAMS[- ]?III\.AV\b", re.I), "Safe drinking water"),
    (re.compile(r"safe (drinking )?water", re.I), "Safe drinking water"),

    # Household solar / off-grid electricity
    (re.compile(r"\bAMS[- ]?I\.A\b", re.I), "Household solar"),                   # Electricity generation by the user
    (re.compile(r"solar (home|lantern|lighting|PV)", re.I), "Household solar"),

    # Grid renewables
    (re.compile(r"\bAMS[- ]?I\.D\b|\bAMS[- ]?I\.F\b", re.I), "Grid renewable energy"),
    (re.compile(r"wind|hydro(power)?|geothermal|grid.connected renewable", re.I), "Grid renewable energy"),
    (re.compile(r"utility.scale solar|solar farm", re.I), "Grid renewable energy"),

    # Biogas / anaerobic digestion / methane recovery on farms
    (re.compile(r"\bAMS[- ]?III\.R\b|\bAMS[- ]?III\.D\b", re.I), "Biogas & methane"),
    (re.compile(r"biogas|anaerobic digest|methane recovery|manure", re.I), "Biogas & methane"),

    # Wastewater
    (re.compile(r"\bAMS[- ]?III\.H\b|wastewater", re.I), "Wastewater"),

    # Waste / landfill
    (re.compile(r"\bAMS[- ]?III\.E\b|landfill|composting|municipal solid waste", re.I), "Waste"),

    # Forestry / ARR / avoided deforestation
    (re.compile(r"afforestation|reforestation|\bARR\b|\bAFOLU\b|forestry|avoided deforest", re.I), "Forestry & land use"),

    # Building efficiency / lighting
    (re.compile(r"\bAMS[- ]?II\.C\b|LED|efficient lighting|building efficiency", re.I), "Energy efficiency"),

    # Charcoal
    (re.compile(r"\bAMS[- ]?III\.BG\b|sustainable charcoal", re.I), "Sustainable charcoal"),
]

UNCATEGORISED = "Uncategorised"
UNKNOWN_METHODOLOGY = "Unknown methodology"

# Name-only patterns for the fallback case where BOTH the project's own methodology and
# its parent PoA's methodology are null (a large fraction of older VPAs — the GS API
# never exposed the methodology for them, it only lives in the PDD PDF). These lean
# harder on obvious wording so we don't mis-categorise on a passing mention.
_NAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"improved (kitchen|cookstove|cooking)|clean cooking|efficient cookstove|\bICS\b", re.I), "Clean cooking"),
    (re.compile(r"safe (drinking )?water|clean water|water (supply|purification|filtration)|\bWATER\b.*(borehole|kiosk)", re.I), "Safe drinking water"),
    (re.compile(r"borehole", re.I), "Safe drinking water"),
    (re.compile(r"solar (home|lantern|lighting|lamp|kit)|off.grid solar", re.I), "Household solar"),
    (re.compile(r"biogas|anaerobic digest", re.I), "Biogas & methane"),
    (re.compile(r"wind farm|hydropower|geothermal", re.I), "Grid renewable energy"),
    (re.compile(r"afforestation|reforestation|forest restor", re.I), "Forestry & land use"),
    (re.compile(r"sustainable charcoal", re.I), "Sustainable charcoal"),
]


def categorise(methodology: str | None, name: str | None = None) -> str:
    """Categorise a project by methodology first, falling back to name-based cues."""
    if methodology:
        for pattern, label in _RULES:
            if pattern.search(methodology):
                return label
        return UNCATEGORISED
    if name:
        for pattern, label in _NAME_RULES:
            if pattern.search(name):
                return label
    return UNKNOWN_METHODOLOGY


def resolve_methodology(project_methodology: str | None, poa_methodology: str | None) -> str | None:
    """VPAs often carry a null methodology — inherit from the parent PoA when we can."""
    return project_methodology or poa_methodology or None
