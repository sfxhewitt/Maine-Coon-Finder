"""Ask Claude to look at the advert photos and text and decide whether there
is a dark grey Maine Coon kitten for sale. One call also pulls the health
facts out of free-text descriptions."""

from __future__ import annotations

import base64
import logging

import anthropic
from pydantic import BaseModel, Field

from .config import Settings
from .http import PoliteClient
from .models import Assessment, Listing

log = logging.getLogger(__name__)

SUPPORTED_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class _ColourResult(BaseModel):
    dark_grey_kitten_present: bool = Field(
        description="True only if at least one kitten for sale is a dark grey Maine Coon as defined."
    )
    colour_seen: str = Field(description="Short plain-English colour(s) of the kittens, e.g. 'blue smoke, brown tabby'.")
    confidence: float = Field(description="0 to 1: how sure you are about dark_grey_kitten_present.")
    looks_like_maine_coon: bool
    is_crossbreed: bool = Field(description="Text or photos say it is a cross/mix, not pure Maine Coon.")
    grey_kitten_still_available: bool | None = Field(
        description="False if the text says the grey one(s) are sold/reserved; null if unknown."
    )
    age_weeks: float | None = Field(description="Kitten age in weeks if stated or implied (date of birth), else null.")
    vaccinated: bool | None
    wormed: bool | None
    flea_treated: bool | None
    vet_checked: bool | None
    microchipped: bool | None
    registered: bool | None = Field(description="GCCF/TICA/FIFe registered.")
    best_image_index: int = Field(description="0-based index of the photo that best shows the grey kitten (0 if none).")
    reason: str = Field(description="One sentence explaining the colour decision.")
    summary: str = Field(description="Two short sentences for the buyer: what's for sale and anything notable.")


SYSTEM = """You help a UK buyer find a kitten. You look at classified-advert photos and text.

The buyer ONLY wants a DARK GREY Maine Coon kitten (under 1 year). Count as dark grey:
{accepted}.
Do NOT count: light silver or silver tabby, cream, white, black (solid, no grey undercoat), brown/red/ginger tabby, \
tortie, calico, or a kitten that is mostly white. Black smoke counts only if it reads as charcoal/dark grey overall.
Mixed litters are fine as long as at least one dark grey kitten is for sale.
If only a parent (mum/dad) is grey but none of the kittens are, that is NOT a match.
Photos can have odd lighting: use the text too (e.g. 'blue boy', 'blue smoke girl').
Be strict: when unsure, lower confidence rather than guessing yes.
Health facts: use true/false only when the text clearly says so; otherwise null."""


class ColourChecker:
    def __init__(self, s: Settings, http: PoliteClient):
        self.s = s
        self.http = http
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key or None)

    def _image_blocks(self, urls: list[str]) -> tuple[list[dict], list[str]]:
        blocks, used = [], []
        for url in urls:
            if len(blocks) >= self.s.max_images_per_check:
                break
            try:
                data, ctype = self.http.get_bytes(url, max_bytes=4_500_000)
            except Exception as e:  # noqa: BLE001 - a bad image shouldn't stop the check
                log.debug("image fetch failed %s: %s", url, e)
                continue
            if ctype not in SUPPORTED_MEDIA:
                ctype = "image/jpeg"
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": ctype,
                        "data": base64.standard_b64encode(data).decode(),
                    },
                }
            )
            used.append(url)
        return blocks, used

    def assess(self, listing: Listing) -> tuple[Assessment, list[str]]:
        images, used = self._image_blocks(listing.images)
        facts = [
            f"Site: {listing.source}",
            f"Title: {listing.title}",
            f"Price: £{listing.price_gbp:.0f}" if listing.price_gbp else "Price: not stated",
            f"Age (from site): {listing.age_days} days" if listing.age_days is not None else "Age: not stated",
            f"Site health fields: {listing.health}" if listing.health else "",
            f"Description:\n{listing.description[:6000]}",
            f"{len(images)} photo(s) attached, in order." if images else "No photos could be loaded.",
        ]
        content = images + [{"type": "text", "text": "\n".join(f for f in facts if f)}]
        resp = self.client.beta.messages.parse(
            model=self.s.claude_model,
            max_tokens=4000,
            system=SYSTEM.format(accepted=", ".join(self.s.accepted_colours)),
            messages=[{"role": "user", "content": content}],
            output_format=_ColourResult,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"Claude gave no usable answer (stop_reason={resp.stop_reason})")
        r: _ColourResult = resp.parsed_output
        idx = r.best_image_index if 0 <= r.best_image_index < len(used) else 0
        return (
            Assessment(
                is_match=r.dark_grey_kitten_present,
                colour=r.colour_seen,
                confidence=r.confidence,
                is_maine_coon=r.looks_like_maine_coon,
                is_crossbreed=r.is_crossbreed,
                reason=r.reason,
                age_weeks=r.age_weeks,
                vaccinated=r.vaccinated,
                wormed=r.wormed,
                flea_treated=r.flea_treated,
                vet_checked=r.vet_checked,
                microchipped=r.microchipped,
                registered=r.registered,
                grey_kitten_still_available=r.grey_kitten_still_available,
                best_image_index=idx,
                summary=r.summary,
            ),
            used,
        )
