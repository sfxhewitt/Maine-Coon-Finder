"""Work out how to reach a seller, and write the message to send them."""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from .config import Settings
from .models import Listing

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# UK mobile / landline, with or without +44 and spaces.
PHONE_RE = re.compile(r"(?<!\d)(?:\+44\s?7\d{3}|07\d{3}|\+44\s?[12]\d{2,3}|0[12]\d{2,3})[\s\-]?\d{3}[\s\-]?\d{3,4}(?!\d)")
IGNORED_EMAIL_DOMAINS = {"pets4homes.co.uk", "gumtree.com", "preloved.co.uk", "example.com"}

DEFAULT_TEMPLATE = """Hi${seller_greeting},

I've just seen your advert "${title}" on ${site} and I'd love to buy your dark grey Maine Coon kitten if it's still available.

I'm ready to move quickly: I can pay the deposit straight away and arrange to view/collect at a time that suits you.
Could you let me know if it's still available and the next steps?

Advert: ${url}

Many thanks,
${buyer_name}
${buyer_phone}
"""

SITE_NAMES = {"pets4homes": "Pets4Homes", "gumtree": "Gumtree", "preloved": "Preloved"}


def find_email(listing: Listing) -> str | None:
    if listing.contact_email:
        return listing.contact_email
    for m in EMAIL_RE.findall(listing.text):
        if m.split("@", 1)[1].lower() not in IGNORED_EMAIL_DOMAINS:
            return m
    return None


def find_phone(listing: Listing) -> str | None:
    if listing.contact_phone:
        return listing.contact_phone
    m = PHONE_RE.search(listing.text)
    return m.group(0) if m else None


def load_template(s: Settings) -> str:
    p = Path(s.email_template_path)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return DEFAULT_TEMPLATE


def render_message(listing: Listing, s: Settings) -> tuple[str, str]:
    """Return (subject, body)."""
    first = listing.seller_name.split()[0] if listing.seller_name else ""
    values = {
        "seller_greeting": f" {first}" if first else "",
        "seller_name": listing.seller_name or "there",
        "title": listing.title,
        "site": SITE_NAMES.get(listing.source, listing.source),
        "url": listing.url,
        "price": f"£{listing.price_gbp:.0f}" if listing.price_gbp else "",
        "buyer_name": s.buyer_name,
        "buyer_phone": s.buyer_phone,
        "buyer_location": s.buyer_location,
    }
    body = Template(load_template(s)).safe_substitute(values)
    # Drop lines that ended up empty because a value wasn't set (e.g. no phone).
    body = re.sub(r"\n[ \t]+\n", "\n\n", body).rstrip() + "\n"
    subject = f"Your Maine Coon kitten advert: {listing.title}"[:150]
    return subject, body
