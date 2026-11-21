"""Gumtree: browse pages under /pets/ (robots.txt disallows /search, so we use
the category pages instead). The results page lists advert links in JSON-LD;
each advert page has a schema.org Product block with price, images, age and
health fields. Sellers are contacted through Gumtree's reply form."""

from __future__ import annotations

import re
from typing import Iterable

from ..models import Listing
from .base import Source, ld_json_blocks, parse_age_text, parse_price

BASE = "https://www.gumtree.com"
SEARCHES = [
    BASE + "/pets/pets-for-sale/cats/maine-coon/uk?sort=date",
    BASE + "/pets/pets-for-sale/cats/uk?q=maine+coon&sort=date",
]
ID_RE = re.compile(r"/(\d{6,})(?:$|[/?#])")

HEALTH_FIELDS = {
    "vaccinated": "vaccinated",
    "deflead": "flea_treated",
    "microchipped": "microchipped",
    "neutered / spayed": "neutered",
    "health checked by a vet": "vet_checked",
    "gccf/tica/fife registered": "registered",
    "wormed": "wormed",
    "dewormed": "wormed",
}


class Gumtree(Source):
    name = "gumtree"
    contact_note = (
        "Gumtree hides seller email addresses, so I can't email this seller directly. "
        "Tap the advert, press Message, and paste the text below."
    )

    def search(self, page: int = 1) -> Iterable[Listing]:
        seen: set[str] = set()
        for base_url in SEARCHES:
            url = base_url + (f"&page={page}" if page > 1 else "")
            html = self.http.get(url).text
            for block in ld_json_blocks(html):
                for node in block.get("@graph", [block]) if isinstance(block, dict) else []:
                    if node.get("@type") != "ItemList":
                        continue
                    for el in node.get("itemListElement", []):
                        link = el.get("url", "")
                        m = ID_RE.search(link)
                        if not m or "/p/cats/" not in link or m.group(1) in seen:
                            continue
                        seen.add(m.group(1))
                        yield Listing(
                            source=self.name,
                            source_id=m.group(1),
                            url=link,
                            title=el.get("name", "").strip(),
                        )

    def enrich(self, listing: Listing) -> Listing:
        html = self.http.get(listing.url).text
        product = next(
            (b for b in ld_json_blocks(html) if isinstance(b, dict) and b.get("@type") == "Product"),
            None,
        )
        if not product:
            listing.enriched = True
            return listing
        listing.title = product.get("name", listing.title).strip()
        listing.description = (product.get("description") or "").strip()
        offers = product.get("offers") or {}
        listing.price_gbp = parse_price(offers.get("price"))
        listing.location = offers.get("availableAtOrFrom", "") or ""
        imgs = product.get("image") or []
        listing.images = [imgs] if isinstance(imgs, str) else list(imgs)
        for prop in product.get("additionalProperty") or []:
            name = str(prop.get("name", "")).strip().lower()
            value = str(prop.get("value", "")).strip()
            if name == "breed" and "maine" in value.lower():
                listing.is_maine_coon_breed_field = True
            elif name == "age":
                listing.age_days = parse_age_text(value)
            elif name in HEALTH_FIELDS:
                listing.health[HEALTH_FIELDS[name]] = value.lower().startswith("y")
        listing.enriched = True
        return listing
