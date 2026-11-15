"""Pets4Homes: the biggest UK pet site. Search pages embed their data as JSON
(Next.js __NEXT_DATA__), so no fragile HTML scraping. robots.txt allows /sale/
and /classifieds/. Sellers are contacted through Pets4Homes chat (login needed);
email addresses are not shown."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..models import Listing
from .base import Source, next_data

BASE = "https://www.pets4homes.co.uk"
# Kittens 0-3 months (1) and 4-12 months (2), newest first.
SEARCH = BASE + "/sale/kittens/maine-coon/?sort=newest-first&dateOfBirth=1&dateOfBirth=2"

HEALTH_KEYS = {
    "vaccinations": "vaccinated",
    "treatedForWorms": "wormed",
    "fleaTreated": "flea_treated",
    "healthChecked": "vet_checked",
    "microchipped": "microchipped",
    "neutered": "neutered",
    "parentsHealthTested": "parents_health_tested",
}


def _image(url: str) -> str:
    return url.replace("##NAME##.jpeg", "originalImage.jpeg?width=1080").replace(
        "##NAME##", "originalImage"
    )


def _attrs(raw: list[dict]) -> dict[str, str]:
    return {a.get("key"): str(a.get("value")) for a in raw or []}


def _apply_attrs(listing: Listing, attrs: dict[str, str]) -> None:
    if attrs.get("breed") == "pets.cats.breed.maineCoon":
        listing.is_maine_coon_breed_field = True
    dob = attrs.get("dateOfBirth")
    if dob and dob.isdigit():
        born = datetime.fromtimestamp(int(dob) / 1000, tz=timezone.utc)
        listing.age_days = max(0, (datetime.now(timezone.utc) - born).days)
    if attrs.get("registered"):
        listing.health["registered"] = True
    for k, name in HEALTH_KEYS.items():
        if k in attrs:
            listing.health[name] = attrs[k] == "true"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class Pets4Homes(Source):
    name = "pets4homes"
    contact_note = (
        "Pets4Homes only lets buyers message through its own chat (you need to be "
        "logged in), so I can't email this seller. Tap the advert, press Message, "
        "and paste the text below."
    )

    def _to_listing(self, item: dict) -> Listing:
        gi = item.get("generalInformation") or {}
        price = None if gi.get("hidePrice") else (gi.get("price") or {}).get("amount")
        loc = item.get("locationV3") or {}
        coords = loc.get("coordinates") or {}
        user = item.get("user") or {}
        names = user.get("generalInformations") or {}
        imgs = [i.get("originalImage") or i.get("url") for i in item.get("images") or []]
        listing = Listing(
            source=self.name,
            source_id=str(item["id"]),
            url=f"{BASE}/classifieds/{item['slug']}/",
            title=gi.get("title", "").strip(),
            description=(gi.get("description") or item.get("displayDescription") or "").strip(),
            price_gbp=float(price) if price else None,
            location=", ".join(x for x in [loc.get("postalTown"), loc.get("adminRegion2")] if x),
            lat=coords.get("latitude"),
            lon=coords.get("longitude"),
            images=[_image(u) for u in imgs if u],
            published_at=_parse_dt(item.get("publishedAt")),
            seller_name=" ".join(x for x in [names.get("firstName"), names.get("lastName")] if x),
            seller_type=((names.get("userType") or {}).get("type") or "").lower(),
        )
        _apply_attrs(listing, _attrs(item.get("attributes")))
        return listing

    def search(self, page: int = 1) -> Iterable[Listing]:
        url = SEARCH + (f"&page={page}" if page > 1 else "")
        props = next_data(self.http.get(url).text)["props"]["pageProps"]
        items = (
            (props.get("featuredListings") or [])
            + (props.get("boostedListings") or [])
            + (props.get("regularListings") or [])
        )
        seen = set()
        for item in items:
            if item.get("id") in seen or item.get("status") not in (None, "Active"):
                continue
            seen.add(item.get("id"))
            yield self._to_listing(item)

    def enrich(self, listing: Listing) -> Listing:
        props = next_data(self.http.get(listing.url).text)["props"]["pageProps"]
        full = self._to_listing(props["listing"])
        # Keep everything from the full page, but not lose search-page facts.
        full.health = {**listing.health, **full.health}
        litter = props.get("petsInLitter") or []
        available = [p for p in litter if p.get("status") == "Available"]
        if litter:
            prices = [(p.get("price") or {}).get("amount") for p in available]
            prices = [float(p) for p in prices if p]
            if prices:
                full.price_gbp = min(prices)
            for p in available:
                for img in (p.get("media") or {}).get("images") or []:
                    if img.get("url"):
                        full.images.append(_image(img["url"]))
            if not available:
                full.description += "\n[Pets4Homes: no kittens in this litter are marked Available]"
        full.enriched = True
        return full
