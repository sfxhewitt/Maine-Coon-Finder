from maine_coon_finder import filters
from maine_coon_finder.config import Settings
from maine_coon_finder.contact import find_email, find_phone, render_message
from maine_coon_finder.models import Assessment, Listing


def L(**kw) -> Listing:
    base = dict(source="gumtree", source_id="1", url="https://x/1", title="Maine Coon kittens", enriched=True,
                images=["https://img/1.jpg"])
    base.update(kw)
    return Listing(**base)


def A(**kw) -> Assessment:
    base = dict(is_match=True, colour="blue smoke", confidence=0.9, is_maine_coon=True, is_crossbreed=False, reason="")
    base.update(kw)
    return Assessment(**base)


S = Settings()


def test_prefilter_hard_rules():
    assert filters.prefilter(L(), S, None).ok
    assert not filters.prefilter(L(title="Ragdoll kittens"), S, None).ok
    assert not filters.prefilter(L(title="Maine coon x ragdoll kittens"), S, None).ok
    assert not filters.prefilter(L(title="Half maine coon half siberian"), S, None).ok
    assert not filters.prefilter(L(price_gbp=1200), S, None).ok
    assert filters.prefilter(L(price_gbp=1000), S, None).ok
    assert filters.prefilter(L(price_gbp=None), S, None).ok  # unknown price is allowed through
    assert not filters.prefilter(L(age_days=400), S, None).ok
    assert not filters.prefilter(L(description="She is 3 years old"), S, None).ok
    assert not filters.prefilter(L(title="Maine coon stud service available"), S, None).ok
    assert not filters.prefilter(L(description="All now sold, thank you"), S, None).ok
    # Breed field from the site counts even if the title doesn't say it.
    assert filters.prefilter(L(title="Gorgeous kittens", is_maine_coon_breed_field=True), S, None).ok


def test_prefilter_distance():
    s = Settings(max_distance_miles=50)
    assert filters.prefilter(L(), s, 30).ok
    assert not filters.prefilter(L(), s, 120).ok
    assert filters.prefilter(L(), s, None).ok  # unknown distance is let through


def test_no_photos_needs_grey_words():
    assert not filters.prefilter(L(images=[], description="black and white kittens"), S, None).ok
    assert filters.prefilter(L(images=[], description="one blue boy left"), S, None).ok


def test_final_check():
    l = L(age_days=70)
    assert filters.final_check(l, A(), S).ok
    assert not filters.final_check(l, A(is_match=False, colour="brown tabby"), S).ok
    assert not filters.final_check(l, A(confidence=0.3), S).ok
    assert not filters.final_check(l, A(is_crossbreed=True), S).ok
    assert not filters.final_check(l, A(grey_kitten_still_available=False), S).ok
    assert not filters.final_check(L(), A(age_weeks=60), S).ok


def test_health_merge_and_score():
    l = L(health={"vaccinated": True}, price_gbp=450)
    h = filters.health_facts(l, A(wormed=True, vaccinated=False))
    assert h["vaccinated"] is True  # site field wins
    assert h["wormed"] is True
    assert filters.score(l, A(), S) > filters.score(L(price_gbp=990), A(), S)


def test_contact_extraction_and_message():
    l = L(description="Call 07712 345678 or email jo.breeder@example.co.uk", seller_name="Jo B")
    assert find_email(l) == "jo.breeder@example.co.uk"
    assert find_phone(l).replace(" ", "") == "07712345678"
    assert find_email(L(description="no contact here")) is None
    s = Settings(buyer_name="Daniel", buyer_phone="07000 111222", email_template_path="/nonexistent")
    subject, body = render_message(l, s)
    assert "Hi Jo," in body and "Daniel" in body and l.url in body
    assert "Maine Coon" in subject


def test_text_only_fallback():
    assert filters.text_only_assessment(L(description="One blue smoke boy left")).is_match
    assert filters.text_only_assessment(L(description="Charcoal grey girl, 10 weeks")).is_match
    assert not filters.text_only_assessment(L(description="Mum is a blue smoke. Kittens are brown tabby")).is_match
    assert not filters.text_only_assessment(L(description="Red and cream kittens with blue eyes")).is_match
    a = filters.text_only_assessment(L(description="grey boy"))
    assert filters.final_check(L(), a, S).ok
