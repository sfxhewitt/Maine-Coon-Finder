"""Cheap rules that run before the (paid) photo check.

Hard rules reject a listing outright. Soft rules only change how it is ranked
and described in the alert."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings
from .models import Assessment, Listing

MAINE_RE = re.compile(r"\bmaine?[\s\-]*coo?ne?s?\b|\bmc\s+kitten", re.I)
CROSS_RE = re.compile(
    r"\b(cross|crossed|x\s*(ragdoll|siberian|bengal|british|persian|norwegian|turkish|russian|"
    r"angora|moggy|domestic)|half\s+maine|mix(ed)?\s+breed|maine\s*coon\s*mix|\w+\s*x\s*maine)\b",
    re.I,
)
# Phrases that mean it is not a kitten for sale.
NOT_FOR_SALE_RE = re.compile(
    r"\b(wanted|looking\s+for\s+a\s+(maine|kitten)|stud\s+(service|available)|"
    r"retired\s+(queen|stud|breeding)|for\s+stud)\b",
    re.I,
)
ADULT_RE = re.compile(r"\b([1-9]\d?)\s*(years?|yrs?)\s*old\b", re.I)
GREYISH_RE = re.compile(r"\b(gr[ae]y|blue|smoke|smokey|smoky|charcoal|slate|silver)\b", re.I)
PEDIGREE_RE = re.compile(
    r"\b(pedigree|pedigrees|gccf|tica|fife|registered|registred|registed|reg\.?\s*(with|papers)|"
    r"(with|full|comes?\s+with)\s+(pedigree\s+)?papers|champion\s+(lines?|bloodlines?))\b",
    re.I,
)
NOT_PEDIGREE_RE = re.compile(
    r"\b(non[\s\-]*pedigree|not\s+(gccf\s+|tica\s+)?(pedigree|registered)|unregistered|no\s+papers|"
    r"without\s+papers|not\s+registered|can'?t\s+be\s+registered)\b",
    re.I,
)
SOLD_RE = re.compile(r"\b(all\s+(now\s+)?(sold|reserved)|sold\s+out|no\s+longer\s+available)\b", re.I)


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


def prefilter(listing: Listing, s: Settings, distance_miles: float | None) -> Verdict:
    """Hard rules that need no photo. Runs on search-page data and again after
    the advert page has been fetched."""
    text = listing.text
    if not (listing.is_maine_coon_breed_field or MAINE_RE.search(text)):
        return Verdict(False, "not a Maine Coon")
    if not s.allow_crossbreeds and CROSS_RE.search(text):
        return Verdict(False, "crossbreed")
    if NOT_FOR_SALE_RE.search(listing.title):
        return Verdict(False, "not a kitten for sale")
    if SOLD_RE.search(text):
        return Verdict(False, "sold")
    if listing.price_gbp is not None and listing.price_gbp > s.max_price_gbp:
        return Verdict(False, f"price £{listing.price_gbp:.0f} over cap")
    if listing.age_days is not None and listing.age_days >= s.max_age_days:
        return Verdict(False, f"{listing.age_days} days old")
    if listing.age_days is None and ADULT_RE.search(text):
        return Verdict(False, "adult cat")
    if (
        s.max_distance_miles is not None
        and distance_miles is not None
        and distance_miles > s.max_distance_miles
    ):
        return Verdict(False, f"{distance_miles:.0f} miles away")
    if s.require_pedigree and listing.enriched and not is_pedigree(listing):
        return Verdict(False, "not stated as pedigree")
    if listing.enriched and not listing.images and not GREYISH_RE.search(text):
        # No photos to check and the text never mentions a grey-family colour.
        # (With photos we always let Claude look: text often only names the parents.)
        return Verdict(False, "no photos and no grey/blue/smoke in the text")
    return Verdict(True)


def is_pedigree(listing: Listing) -> bool:
    """Stated as pedigree: GCCF/TICA/FIFe registered, pedigree papers, or the site's
    'registered' field. An advert that says it isn't registered never counts."""
    text = listing.text
    if NOT_PEDIGREE_RE.search(text) or listing.health.get("registered") is False and not PEDIGREE_RE.search(text):
        return False
    return bool(listing.health.get("registered")) or bool(PEDIGREE_RE.search(text))


def final_check(listing: Listing, a: Assessment, s: Settings) -> Verdict:
    """Hard rules applied to Claude's reading of the photos and text."""
    if not a.is_match:
        return Verdict(False, f"colour: {a.colour or 'not dark grey'}")
    if a.confidence < s.min_colour_confidence:
        return Verdict(False, f"colour confidence {a.confidence:.2f} too low")
    if not a.is_maine_coon:
        return Verdict(False, "doesn't look like a Maine Coon")
    if a.is_crossbreed and not s.allow_crossbreeds:
        return Verdict(False, "crossbreed")
    if a.grey_kitten_still_available is False:
        return Verdict(False, "grey kitten already sold/reserved")
    age_days = listing.age_days
    if age_days is None and a.age_weeks is not None:
        age_days = int(a.age_weeks * 7)
    if age_days is not None and age_days >= s.max_age_days:
        return Verdict(False, f"{age_days} days old")
    return Verdict(True)


def health_facts(listing: Listing, a: Assessment | None) -> dict[str, bool | None]:
    """Merge site fields with what Claude read in the description."""
    keys = ["vaccinated", "wormed", "flea_treated", "vet_checked", "microchipped", "registered"]
    out: dict[str, bool | None] = {}
    for k in keys:
        v = listing.health.get(k)
        if v is None and a is not None:
            v = getattr(a, k, None)
        out[k] = v
    return out


def score(listing: Listing, a: Assessment | None, s: Settings) -> int:
    """Higher is better. Used to label alerts, not to reject."""
    pts = 0
    h = health_facts(listing, a)
    pts += sum(2 for v in h.values() if v)
    if listing.price_gbp is not None and listing.price_gbp <= s.preferred_price_gbp:
        pts += 4
    if a is not None:
        pts += int(a.confidence * 4)
    return pts


# Text-only mode (no ANTHROPIC_API_KEY): only clearly dark-grey wording counts.
DARK_GREY_TEXT_RE = re.compile(
    r"\b(blue(\s+smoke)?|gr[ae]y|charcoal|black\s+smoke|slate)\b(?!\s+eyes)", re.I
)
NOT_KITTEN_COLOUR_RE = re.compile(r"\b(mum|mother|dad|father|dam|sire|queen|stud)\b[^.\n]{0,40}$", re.I)


def text_only_assessment(listing: Listing) -> Assessment:
    """Fallback when there's no Claude key: decide from the advert's words alone.
    Misses adverts that only show the colour in photos, so it's a weaker check."""
    text = listing.text
    hits = []
    for m in DARK_GREY_TEXT_RE.finditer(text):
        before = text[max(0, m.start() - 60):m.start()]
        if NOT_KITTEN_COLOUR_RE.search(before):
            continue  # e.g. "mum is a blue smoke"
        hits.append(m.group(0).lower())
    found = bool(hits)
    return Assessment(
        is_match=found,
        colour=(", ".join(sorted(set(hits))) + " (from text; photo not checked)") if found else "no dark grey in text",
        confidence=0.7 if found else 0.0,
        is_maine_coon=True,
        is_crossbreed=False,
        reason="text-only check",
    )
