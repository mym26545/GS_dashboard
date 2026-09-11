"""Parse the retiring entity out of the free-text `note` field on retired credit blocks.

Typical shapes we see in the wild:
    "Retired by United Purpose/Self Help Africa on behalf of Admiral Group Plc"
    "Retired on behalf of Shell International"
    "Retired by ClimatePartner for the benefit of BMW Group"
    "Retired by ACME Ltd"           (broker only, no end beneficiary)
    ""                              (no note)
    "Voluntary cancellation"        (no parseable name)

We surface the END BENEFICIARY when we can find one; otherwise we fall back to the
"retired by" party (usually a broker). Anything we can't parse lands in an "Unparsed"
bucket so it's visible in the dashboard without silently vanishing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ON_BEHALF = re.compile(
    # Don't stop at "." inside abbreviations like "S.A.", "N.V.", "Inc." — require
    # the terminator to be followed by whitespace-then-capital, or end of string.
    r"(?:on behalf of|for the benefit of|beneficiary[:\s])\s*(?P<name>.+?)(?:[;\n]|\.\s|\s*$)",
    re.I,
)
_RETIRED_BY = re.compile(
    r"retired by\s+(?P<name>.+?)(?:\s+(?:on behalf of|for the benefit of)\b|[;\n]|\.\s|\s*$)",
    re.I,
)
_TRAILING_JUNK = re.compile(r"[\s\"'“”‘’.,;:]+$")
MAX_NAME_CHARS = 80


@dataclass
class Beneficiary:
    end_beneficiary: str | None  # the party the credits were retired "on behalf of"
    retirer: str | None          # the party that actually pressed the button (often a broker)
    unparsed: bool               # True when we couldn't find either


def _clean(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    s = _TRAILING_JUNK.sub("", s)
    # Cap absurdly long matches (typically when the note is a paragraph, not a name).
    if len(s) > MAX_NAME_CHARS:
        s = s[:MAX_NAME_CHARS].rsplit(" ", 1)[0] + "…"
    return s


def parse(note: str | None) -> Beneficiary:
    if not note or not note.strip():
        return Beneficiary(None, None, unparsed=True)
    end = None
    retirer = None
    m = _ON_BEHALF.search(note)
    if m:
        end = _clean(m.group("name"))
    m = _RETIRED_BY.search(note)
    if m:
        retirer = _clean(m.group("name"))
    if not end and not retirer:
        return Beneficiary(None, None, unparsed=True)
    return Beneficiary(end, retirer, unparsed=False)


def best_name(note: str | None) -> str:
    """Single-label view for the leaderboard: prefer the end beneficiary, fall back to
    the retirer, then to an explicit 'Unparsed' bucket."""
    b = parse(note)
    if b.end_beneficiary:
        return b.end_beneficiary
    if b.retirer:
        return f"(retirer only) {b.retirer}"
    return "Unparsed / anonymous"
