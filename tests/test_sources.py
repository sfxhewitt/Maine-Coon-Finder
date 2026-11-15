from conftest import FakeHttp

from maine_coon_finder.sources.base import parse_age_text, parse_price
from maine_coon_finder.sources.gumtree import Gumtree
from maine_coon_finder.sources.pets4homes import Pets4Homes
from maine_coon_finder.sources.preloved import Preloved


def test_pets4homes_search_and_enrich():
    http = FakeHttp({"/sale/kittens/": "p4h_search.html", "/classifieds/": "p4h_detail.html"})
    src = Pets4Homes(http)
    found = list(src.search(1))
    assert len(found) == 3
    l = found[0]
    assert l.source == "pets4homes" and l.url.startswith("https://www.pets4homes.co.uk/classifieds/")
    assert l.is_maine_coon_breed_field
    assert l.price_gbp and l.price_gbp > 0
    assert l.images and "##NAME##" not in l.images[0]
    assert l.age_days is not None
    full = src.enrich(l)
    assert full.enriched
    assert full.health.get("vaccinated") is True
    assert full.health.get("wormed") is True
    assert full.lat is not None
    assert "sort=newest-first" in http.calls[0]


def test_gumtree_search_and_enrich():
    http = FakeHttp({"/pets/pets-for-sale/": "gumtree_search.html", "/p/cats/": "gumtree_detail.html"})
    src = Gumtree(http)
    found = list(src.search(1))
    assert found, "should find adverts in the JSON-LD list"
    assert all("/p/cats/" in l.url for l in found)
    assert len({l.source_id for l in found}) == len(found)  # deduped across both searches
    full = src.enrich(found[0])
    assert full.price_gbp == 500.0
    assert full.age_days == 77
    assert full.is_maine_coon_breed_field
    assert full.health["vaccinated"] is False
    assert full.health["flea_treated"] is True
    assert full.images


def test_preloved_search():
    http = FakeHttp({"/account/api/classifieds": "preloved_search.json"})
    found = list(Preloved(http).search(1))
    assert len(found) == 3
    l = found[0]
    assert l.url.startswith("https://www.preloved.co.uk/adverts/show/")
    assert l.lat is not None and l.price_gbp
    assert l.enriched


def test_parsers():
    assert parse_age_text("11 weeks") == 77
    assert parse_age_text("3 months old") == 91
    assert parse_age_text("1 year") == 365
    assert parse_age_text("ready now") is None
    assert parse_price("£1,250.00") == 1250.0
    assert parse_price("0") is None
    assert parse_price(None) is None
