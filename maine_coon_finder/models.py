"""Core data types shared by every part of the finder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Listing:
    """One advert from one site, normalised into a common shape."""

    source: str  # e.g. "pets4homes"
    source_id: str  # the site's own id for the advert
    url: str
    title: str
    description: str = ""
    price_gbp: float | None = None  # None = not stated
    location: str = ""  # human readable, e.g. "Sittingbourne, Kent"
    lat: float | None = None
    lon: float | None = None
    age_days: int | None = None  # None = unknown
    images: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    seller_name: str = ""
    seller_type: str = ""  # breeder / private / ...
    # Health facts the site states as structured fields: key -> True/False.
    health: dict[str, bool] = field(default_factory=dict)
    is_maine_coon_breed_field: bool = False  # site's breed field says Maine Coon
    contact_email: str | None = None
    contact_phone: str | None = None
    # Has the site's own detail page already been fetched?
    enriched: bool = False

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.description}"


@dataclass
class Assessment:
    """Result of the photo + text check by Claude."""

    is_match: bool
    colour: str  # what colour(s) were seen, in plain words
    confidence: float  # 0..1
    is_maine_coon: bool
    is_crossbreed: bool
    reason: str
    age_weeks: float | None = None
    vaccinated: bool | None = None
    wormed: bool | None = None
    flea_treated: bool | None = None
    vet_checked: bool | None = None
    microchipped: bool | None = None
    registered: bool | None = None
    grey_kitten_still_available: bool | None = None
    best_image_index: int = 0
    summary: str = ""
