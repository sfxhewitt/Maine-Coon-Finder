"""End-to-end behaviour of the alert -> Yes/No -> email flow, with fakes for
the sites, Claude, Telegram and SMTP. The key promise tested here: an email is
only ever sent after an explicit Yes, and only once."""

from __future__ import annotations

import pytest
from conftest import FakeHttp

from maine_coon_finder.app import Finder
from maine_coon_finder.config import Settings
from maine_coon_finder.models import Assessment, Listing
from maine_coon_finder.sources.base import Source

CHAT = "12345"


class FakeChecker:
    def __init__(self, match=True):
        self.match = match
        self.calls = 0

    def assess(self, listing):
        self.calls += 1
        return (
            Assessment(self.match, "blue smoke" if self.match else "brown tabby", 0.9, True, False, "x",
                       summary="Blue smoke boy."),
            listing.images[:1],
        )


class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[tuple] = []
        self.answers: list[str] = []
        self._id = 100

    def send_photo(self, photo, caption, keyboard=None, photo_url=None):
        self._id += 1
        self.sent.append({"type": "photo", "caption": caption, "keyboard": keyboard, "id": self._id})
        return {"message_id": self._id}

    def send_text(self, text, reply_to=None, keyboard=None, chat_id=None):
        self._id += 1
        self.sent.append({"type": "text", "text": text, "keyboard": keyboard, "id": self._id})
        return {"message_id": self._id}

    def edit_caption(self, message_id, caption, keyboard=None):
        self.edits.append((message_id, caption))

    def answer_callback(self, cid, text=""):
        self.answers.append(text)


class FakeMailer:
    def __init__(self, fail=False):
        self.sent: list[tuple] = []
        self.fail = fail

    def send(self, to, subject, body):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, body))
        return "<id>"


class StaticSource(Source):
    name = "gumtree"

    def __init__(self, listings):
        super().__init__(FakeHttp({}))
        self.listings = listings

    def search(self, page=1):
        return list(self.listings) if page == 1 else []


def make(tmp_path, listings, match=True, mail_fail=False):
    s = Settings(
        telegram_token="t", telegram_chat_id=CHAT, anthropic_api_key="k", db_path=str(tmp_path / "db.sqlite3"),
        buyer_name="Daniel", smtp_user="me@example.com", smtp_password="pw", email_from="me@example.com",
        sources=["gumtree"], email_template_path="/nonexistent",
    )
    tg, mailer, checker = FakeTelegram(), FakeMailer(fail=mail_fail), FakeChecker(match)
    f = Finder(s, colour_checker=checker, telegram=tg, mailer=mailer)
    f.http = FakeHttp({})
    f.sources[0].source = StaticSource(listings)
    return f, tg, mailer, checker


def listing(i="1", desc="Blue boy available, vaccinated. Email seller@breeder.example"):
    return Listing(source="gumtree", source_id=i, url=f"https://www.gumtree.com/p/cats/x/{i}", title="Maine Coon kitten",
                   description=desc, price_gbp=600, age_days=70, images=["https://img/1.jpg"], enriched=True)


def tap(f, tg, action, key, chat=CHAT, msg_id=None):
    msg_id = msg_id or tg.sent[0]["id"]
    f.handle_callback({"id": "cb", "data": f"{action}|{key}", "message": {"message_id": msg_id, "chat": {"id": int(chat)}}})


def test_match_alerts_but_never_emails_by_itself(tmp_path):
    f, tg, mailer, _ = make(tmp_path, [listing()])
    stats = f.scan_source(f.sources[0])
    assert stats == {"notified": 1}
    assert len(tg.sent) == 1 and tg.sent[0]["type"] == "photo"
    assert "blue smoke" in tg.sent[0]["caption"]
    assert mailer.sent == []  # no email without Yes


def test_yes_sends_exactly_one_email(tmp_path):
    l = listing()
    f, tg, mailer, _ = make(tmp_path, [l])
    f.scan_source(f.sources[0])
    tap(f, tg, "y", l.key)
    tap(f, tg, "y", l.key)  # double tap
    assert len(mailer.sent) == 1
    assert mailer.sent[0][0] == "seller@breeder.example"
    assert f.store.get(l.key)["status"] == "contacted"


def test_no_discards_and_never_emails(tmp_path):
    l = listing()
    f, tg, mailer, _ = make(tmp_path, [l])
    f.scan_source(f.sources[0])
    tap(f, tg, "n", l.key)
    tap(f, tg, "y", l.key)  # Yes after No does nothing
    assert mailer.sent == []
    assert f.store.get(l.key)["status"] == "declined"


def test_yes_without_email_gives_paste_ready_message(tmp_path):
    l = listing(desc="Blue boy available, message me on here")
    f, tg, mailer, _ = make(tmp_path, [l])
    f.scan_source(f.sources[0])
    tap(f, tg, "y", l.key)
    assert mailer.sent == []
    last = tg.sent[-1]
    assert last["type"] == "text" and "Gumtree" in last["text"] and "<pre>" in last["text"]


def test_callback_from_other_chat_is_ignored(tmp_path):
    l = listing()
    f, tg, mailer, _ = make(tmp_path, [l])
    f.scan_source(f.sources[0])
    tap(f, tg, "y", l.key, chat="999")
    assert mailer.sent == []
    assert f.store.get(l.key)["status"] == "notified"


def test_email_failure_lets_you_retry(tmp_path):
    l = listing()
    f, tg, mailer, _ = make(tmp_path, [l], mail_fail=True)
    f.scan_source(f.sources[0])
    tap(f, tg, "y", l.key)
    assert f.store.get(l.key)["status"] == "notified"
    assert "Couldn't send" in tg.sent[-1]["text"]


def test_dedupe_and_rejections(tmp_path):
    good, bad = listing("1"), listing("2")
    bad.price_gbp = 5000
    f, tg, _, checker = make(tmp_path, [good, bad])
    f.scan_source(f.sources[0])
    f.scan_source(f.sources[0])  # second pass: nothing new
    assert len(tg.sent) == 1
    assert checker.calls == 1  # over-priced one never reached the paid photo check
    assert f.store.get(bad.key)["status"] == "rejected"


def test_not_grey_is_rejected_quietly(tmp_path):
    f, tg, _, _ = make(tmp_path, [listing()], match=False)
    assert f.scan_source(f.sources[0]) == {"rejected": 1}
    assert tg.sent == []


def test_contact_seller_refuses_without_yes(tmp_path):
    l = listing()
    f, tg, mailer, _ = make(tmp_path, [l])
    f.scan_source(f.sources[0])
    with pytest.raises(PermissionError):
        f.contact_seller(l, None)
    assert mailer.sent == []


class FakeP4H:
    SITE = "Pets4Homes"

    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, url, text):
        from maine_coon_finder.p4h_chat import ChatResult

        self.sent.append((url, text))
        return ChatResult(self.ok, "sent" if self.ok else "chat box didn't open", b"png")


def _p4h_listing():
    return Listing(source="pets4homes", source_id="abc", url="https://www.pets4homes.co.uk/classifieds/abc/",
                   title="Maine Coon kitten", description="blue boy", price_gbp=800, age_days=70,
                   images=["https://img/1.jpg"], enriched=True)


def _wait_threads():
    import threading
    for t in threading.enumerate():
        if t.name == "site-chat":
            t.join(5)


def test_p4h_yes_messages_via_chat_once(tmp_path):
    l = _p4h_listing()
    f, tg, mailer, _ = make(tmp_path, [l])
    f.sources[0].source.name = "pets4homes"
    f.chats["pets4homes"] = FakeP4H()
    f.scan_source(f.sources[0])
    assert "message the seller on Pets4Homes" in tg.sent[0]["caption"]
    assert f.chats["pets4homes"].sent == []  # nothing before Yes
    tap(f, tg, "y", l.key)
    tap(f, tg, "y", l.key)
    _wait_threads()
    assert len(f.chats["pets4homes"].sent) == 1 and mailer.sent == []
    assert f.store.get(l.key)["status"] == "contacted"


def test_p4h_failure_falls_back_to_paste_message(tmp_path):
    l = _p4h_listing()
    f, tg, _, _ = make(tmp_path, [l])
    f.sources[0].source.name = "pets4homes"
    f.chats["pets4homes"] = FakeP4H(ok=False)
    f.scan_source(f.sources[0])
    tap(f, tg, "y", l.key)
    _wait_threads()
    assert "Automatic message failed" in tg.sent[-1]["text"] and "<pre>" in tg.sent[-1]["text"]


def test_p4h_no_never_messages(tmp_path):
    l = _p4h_listing()
    f, tg, _, _ = make(tmp_path, [l])
    f.sources[0].source.name = "pets4homes"
    f.chats["pets4homes"] = FakeP4H()
    f.scan_source(f.sources[0])
    tap(f, tg, "n", l.key)
    _wait_threads()
    assert f.chats["pets4homes"].sent == []
