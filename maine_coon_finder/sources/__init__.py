from __future__ import annotations

from ..http import PoliteClient
from .base import Source
from .gumtree import Gumtree
from .pets4homes import Pets4Homes
from .preloved import Preloved

ALL: dict[str, type[Source]] = {
    Pets4Homes.name: Pets4Homes,
    Gumtree.name: Gumtree,
    Preloved.name: Preloved,
}

# Sites checked but not supported, and why (shown in the README and logs).
UNSUPPORTED = {
    "freeads": "Blocks automated access (403 bot wall). Not bypassed on purpose.",
    "facebook": "Marketplace and groups need a logged-in account; scraping breaks Meta's terms.",
    "kittenlist": "Returns an error page instead of listings (checked Nov 2026).",
}


def build_sources(names: list[str], http: PoliteClient) -> list[Source]:
    out = []
    for n in names:
        cls = ALL.get(n)
        if cls is None:
            raise ValueError(f"Unknown source {n!r}. Known: {', '.join(ALL)}")
        out.append(cls(http))
    return out
