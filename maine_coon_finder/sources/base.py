from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Iterable

from ..http import PoliteClient
from ..models import Listing


class Source(ABC):
    """A site we can poll. Subclasses implement search() and optionally enrich()."""

    name: str = "base"
    # Shown to the user when they tap Yes and there is no email address.
    contact_note: str = "Contact the seller through the advert page."

    def __init__(self, http: PoliteClient):
        self.http = http

    @abstractmethod
    def search(self, page: int = 1) -> Iterable[Listing]:
        """Return the newest Maine Coon adverts on the given results page."""

    def enrich(self, listing: Listing) -> Listing:
        """Fetch the advert page for full details. Default: nothing to add."""
        listing.enriched = True
        return listing


NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
LD_JSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def next_data(html: str) -> dict:
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("page has no __NEXT_DATA__ block (layout changed?)")
    return json.loads(m.group(1))


def ld_json_blocks(html: str) -> list:
    out = []
    for raw in LD_JSON_RE.findall(html):
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def parse_age_text(text: str) -> int | None:
    """Turn '11 weeks', '3 months', '1 year' etc. into days. None if no age found."""
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(week|wk|month|mth|year|yr|day)s?\b", t)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2)
    if unit.startswith("day"):
        return int(n)
    if unit in ("week", "wk"):
        return int(n * 7)
    if unit in ("month", "mth"):
        return int(n * 30.4)
    return int(n * 365)


def parse_price(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", str(value))
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v if v > 0 else None
