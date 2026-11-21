"""Preloved: the site's own pages load results from a JSON endpoint
(/account/api/classifieds), which we call the same way the browser does, at a
low rate. robots.txt disallows /search?keyword and /rest, which we avoid.
Sellers are contacted through Preloved's reply form (login needed)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from ..models import Listing
from .base import Source, parse_age_text

BASE = "https://www.preloved.co.uk"
API = BASE + "/account/api/classifieds?q=maine+coon&advertType=forsale&sectionId=3358"
PAGE_SIZE = 20


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", (title or "").lower())
    return re.sub(r"[\s-]+", "-", s).strip("-") or "advert"


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class Preloved(Source):
    _total: int | None = None  # results count from the last first-page call
    name = "preloved"
    contact_note = (
        "Preloved only lets buyers reply through its own message form, so I can't "
        "email this seller. Tap the advert, press Reply, and paste the text below."
    )

    def search(self, page: int = 1) -> Iterable[Listing]:
        offset = (page - 1) * PAGE_SIZE
        # Preloved answers 503 (not an empty list) when asked for a page past the end,
        # so never ask beyond the total it reported last time.
        if page > 1 and self._total is not None and offset >= self._total:
            return
        url = API + (f"&offset={offset}" if page > 1 else "")
        data = self.http.get(url, headers={"Accept": "application/json"}).json()
        total = (data.get("info") or {}).get("totalResults")
        self._total = int(total) if isinstance(total, (int, float, str)) and str(total).isdigit() else None
        for ad in data.get("adverts", []):
            loc = ad.get("location") or {}
            coords = loc.get("coordinates") or {}
            imgs = [i.get("url") for i in (ad.get("media") or {}).get("images", []) if i.get("url")]
            text = f"{ad.get('title', '')} {ad.get('shortDescription', '')}"
            yield Listing(
                source=self.name,
                source_id=str(ad["id"]),
                # The site 404s without a slug (any slug works).
                url=f"{BASE}/adverts/show/{ad['id']}/{_slug(ad.get('title', ''))}",
                title=(ad.get("title") or "").strip(),
                description=(ad.get("shortDescription") or "").strip(),
                price_gbp=(ad.get("price") or {}).get("value") or None,
                location=loc.get("display", ""),
                lat=coords.get("lat"),
                lon=coords.get("lon"),
                images=imgs,
                published_at=_dt(ad.get("createdDate")),
                seller_type=((ad.get("advertiser") or {}).get("type") or {}).get("value", ""),
                age_days=parse_age_text(text),
                enriched=True,  # the API already gives what we need
            )
