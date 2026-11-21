"""When something breaks, the finder must say so on Telegram and keep alerting,
not go quiet."""

from __future__ import annotations

import json

import anthropic
import httpx2
from test_app import FakeTelegram, listing, make

from maine_coon_finder.config import Settings
from maine_coon_finder.models import Assessment
from maine_coon_finder.telegram import TelegramError
from maine_coon_finder.vision import ClaudeBusy, ClaudeUnavailable, ColourChecker
from conftest import FakeHttp


class BrokenChecker:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def assess(self, l):
        self.calls += 1
        if self.exc:
            raise self.exc
        return Assessment(True, "blue", 0.9, True, False, "x"), l.images[:1]


def test_claude_down_falls_back_to_words_and_warns(tmp_path):
    grey = listing("1", desc="Blue smoke boy available")
    tabby = listing("2", desc="Brown tabby girl")
    f, tg, _, _ = make(tmp_path, [grey, tabby])
    f.checker = BrokenChecker(ClaudeUnavailable("Your credit balance is too low"))
    stats = f.scan_source(f.sources[0])
    assert stats == {"notified": 1, "rejected": 1}
    texts = [m.get("text", "") for m in tg.sent if m["type"] == "text"]
    assert any("photo check isn't working" in t and "credit balance" in t for t in texts)
    assert "photo not checked" in [m for m in tg.sent if m["type"] == "photo"][0]["caption"]
    # Only one warning, however many adverts hit it.
    f.scan_source(f.sources[0])
    assert sum("photo check isn't working" in m.get("text", "") for m in tg.sent) == 1
    assert "failing" in f.photo_check_line()

    # Once Claude works again, the advert judged on words alone gets a proper look.
    f.checker = BrokenChecker(None)
    f.sources[0].source.listings = [listing("3")]
    f.scan_source(f.sources[0])
    assert f.store.get(tabby.key)["status"] == "error"  # queued for re-check
    assert f.claude_problem == ""
    f.sources[0].source.listings = [tabby]
    assert f.scan_source(f.sources[0]) == {"notified": 1}


def test_claude_busy_retries_later_without_using_up_attempts(tmp_path):
    l = listing()
    f, tg, _, _ = make(tmp_path, [l])
    f.checker = BrokenChecker(ClaudeBusy("overloaded"))
    f.scan_due_sources()
    assert f.store.get(l.key) is None  # not recorded, so it's tried again
    f.checker = BrokenChecker(None)
    f.sources[0].next_run = 0
    f.scan_due_sources()
    assert f.store.get(l.key)["status"] == "notified"


def test_photo_rejected_by_telegram_still_alerts_as_text(tmp_path):
    class PickyTelegram(FakeTelegram):
        def send_photo(self, *a, **k):
            raise TelegramError("sendPhoto: Bad Request: IMAGE_PROCESS_FAILED")

    f, _, _, _ = make(tmp_path, [listing()])
    tg = PickyTelegram()
    f.tg = tg
    assert f.scan_source(f.sources[0]) == {"notified": 1}
    assert tg.sent[0]["type"] == "text" and tg.sent[0]["keyboard"]


def test_old_errors_get_another_chance_after_update(tmp_path):
    l = listing()
    f, tg, _, _ = make(tmp_path, [l])
    for _ in range(3):
        f.store.record(l, "error", "boom")
    assert f.store.is_done(l.key)
    f.store.meta_set("requeued_errors_v2", "0")  # as if the database came from the old version
    f2, _, _, _ = make(tmp_path, [l])  # restart on the new version
    assert not f2.store.is_done(l.key)
    assert f2.scan_source(f2.sources[0]) == {"notified": 1}


def test_status_and_daily_summary_explain_quiet_days(tmp_path):
    f, tg, _, _ = make(tmp_path, [listing("1", desc="Brown tabby")], match=False)
    f.scan_due_sources()
    f.handle_message({"chat": {"id": int(CHAT)}, "text": "/status"})
    status = tg.sent[-1]["text"]
    assert "Last 24 hours" in status and "not dark grey 1" in status and "brown tabby ×1" in status
    assert "Photo check: Claude" in status and "gumtree" in status
    f.s.daily_summary_hour_utc = 0
    f.maybe_daily_summary()
    f.maybe_daily_summary()
    assert sum("Daily check-in" in m.get("text", "") for m in tg.sent) == 1


CHAT = "12345"


def _checker_with(status, body):
    def handler(request):
        return httpx2.Response(status, json=body)

    s = Settings(anthropic_api_key="sk-test", max_images_per_check=1)
    c = ColourChecker(s, FakeHttp({}))
    c.client = anthropic.Anthropic(api_key="sk-test", max_retries=0,
                                   http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return c


def test_vision_sorts_api_errors():
    l = listing()
    l.images = []
    cases = [
        (401, "authentication_error", "invalid x-api-key", ClaudeUnavailable),
        (400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API.", ClaudeUnavailable),
        (529, "overloaded_error", "Overloaded", ClaudeBusy),
        (429, "rate_limit_error", "slow down", ClaudeBusy),
    ]
    for status, typ, msg, exc in cases:
        c = _checker_with(status, {"type": "error", "error": {"type": typ, "message": msg}})
        try:
            c.assess(l)
        except exc as e:
            assert msg[:20] in str(e)
        else:
            raise AssertionError(f"{status} should raise {exc.__name__}")


def test_vision_retries_without_photos_when_one_is_unreadable():
    calls = []
    answer = {"dark_grey_kitten_present": True, "colour_seen": "blue", "confidence": 0.8,
              "looks_like_maine_coon": True, "is_crossbreed": False, "grey_kitten_still_available": None,
              "age_weeks": None, "vaccinated": None, "wormed": None, "flea_treated": None, "vet_checked": None,
              "microchipped": None, "registered": None, "best_image_index": 0, "reason": "r", "summary": "s"}

    def handler(request):
        body = json.loads(request.content)
        has_img = any(b["type"] == "image" for b in body["messages"][0]["content"])
        calls.append(has_img)
        if has_img:
            return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                         "message": "Could not process image"}})
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                          "content": [{"type": "text", "text": json.dumps(answer)}],
                                          "stop_reason": "end_turn", "stop_sequence": None,
                                          "usage": {"input_tokens": 1, "output_tokens": 1}})

    s = Settings(anthropic_api_key="sk-test", max_images_per_check=1)
    c = ColourChecker(s, FakeHttp({}))
    c.http.get_bytes = lambda url, max_bytes=0: (b"\xff\xd8junk", "image/jpeg")
    c.client = anthropic.Anthropic(api_key="sk-test", max_retries=0,
                                   http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    a, used = c.assess(listing())
    assert calls == [True, False] and a.is_match and used == []


def test_raising_price_cap_rechecks_adverts_turned_down_for_price(tmp_path):
    cheap_enough = listing("1"); cheap_enough.price_gbp = 1200
    too_dear = listing("2"); too_dear.price_gbp = 1800
    f, _, _, _ = make(tmp_path, [cheap_enough, too_dear])
    f.s.max_price_gbp = 1000
    f.store.meta_set("price_cap_seen", "1000")
    f.scan_source(f.sources[0])
    assert f.store.get(cheap_enough.key)["status"] == "rejected"
    f2, _, _, _ = make(tmp_path, [cheap_enough, too_dear])  # restarted with the default £1500 cap
    assert f2.store.get(cheap_enough.key)["status"] == "error"  # queued for another look
    assert f2.store.get(too_dear.key)["status"] == "rejected"
    assert f2.scan_source(f2.sources[0]) == {"notified": 1, "seen": 1}


def test_first_run_of_price_tracking_assumes_the_old_1000_cap(tmp_path):
    l = listing("1"); l.price_gbp = 1200
    f, _, _, _ = make(tmp_path, [l])
    f.store.record(l, "rejected", "price £1200 over cap")
    f.store._db.execute("DELETE FROM meta WHERE k='price_cap_seen'"); f.store._db.commit()
    f2, _, _, _ = make(tmp_path, [l])
    assert f2.store.get(l.key)["status"] == "error"


def test_a_later_page_failing_does_not_count_as_a_block(tmp_path):
    from maine_coon_finder.http import BlockedError

    f, tg, _, _ = make(tmp_path, [listing()])
    src = f.sources[0].source

    def search(page=1):
        if page > 1:
            raise BlockedError("www.example answered 503")
        return [listing()]

    src.search = search
    f.s.backfill_pages = 3
    f.scan_due_sources()
    assert f.sources[0].last_ok and not f.sources[0].last_problem
    assert f.store.get(listing().key)["status"] == "notified"
