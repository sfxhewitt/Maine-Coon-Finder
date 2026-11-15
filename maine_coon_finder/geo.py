"""Distance from home, using the free postcodes.io API (no key needed)."""

from __future__ import annotations

import logging
import math
import re
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)
API = "https://api.postcodes.io"
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.I)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Geocoder:
    def __init__(self, timeout: float = 10.0):
        self._client = httpx.Client(timeout=timeout)

    @lru_cache(maxsize=2048)
    def lookup(self, place: str) -> tuple[float, float] | None:
        place = (place or "").strip()
        if not place:
            return None
        try:
            m = POSTCODE_RE.search(place)
            if m:
                r = self._client.get(f"{API}/postcodes/{m.group(1).replace(' ', '')}")
                if r.status_code == 200:
                    res = r.json()["result"]
                    return res["latitude"], res["longitude"]
                # Fall back to the outward code (e.g. "LS1").
                out = m.group(1).split()[0] if " " in m.group(1) else m.group(1)[:-3]
                r = self._client.get(f"{API}/outcodes/{out}")
                if r.status_code == 200:
                    res = r.json()["result"]
                    return res["latitude"], res["longitude"]
            # Town names: try the first comma-separated part, then the whole thing.
            for q in [place.split(",")[0].strip(), place]:
                r = self._client.get(f"{API}/places", params={"q": q, "limit": 1})
                if r.status_code == 200 and r.json().get("result"):
                    res = r.json()["result"][0]
                    return res["latitude"], res["longitude"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.debug("geocode failed for %r: %s", place, e)
        return None

    def distance_miles(
        self, home: tuple[float, float] | None, lat: float | None, lon: float | None, place: str
    ) -> float | None:
        if home is None:
            return None
        if lat is None or lon is None:
            found = self.lookup(place)
            if not found:
                return None
            lat, lon = found
        return haversine_miles(home[0], home[1], lat, lon)
