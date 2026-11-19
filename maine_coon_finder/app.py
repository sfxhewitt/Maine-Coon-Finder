"""The finder: poll sources, filter, check photos, alert on Telegram, and act
on the Yes/No buttons."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import filters
from .config import Settings
from .contact import find_email, find_phone, render_message
from .geo import Geocoder
from .http import BlockedError, PoliteClient
from .mailer import Mailer
from .models import Assessment, Listing
from .sources import build_sources
from .sources.base import Source
from .store import Store, listing_from_json
from .telegram import Telegram, TelegramError, esc

log = logging.getLogger(__name__)

WARN_EVERY_SECONDS = 6 * 3600
TEXT_ONLY_FALLBACK = "text only, photo check unavailable"


class _ClaudeBusyStop(Exception):
    """Stop this site's pass for now; the photo check is temporarily overloaded."""


def _ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 3600:
        return f"{max(secs // 60, 0):.0f} min ago"
    if secs < 172800:
        return f"{secs // 3600:.0f} h ago"
    return f"{secs // 86400:.0f} days ago"


def _since(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")

HEALTH_LABELS = {
    "vaccinated": "vaccinated",
    "wormed": "wormed",
    "flea_treated": "flea treated",
    "vet_checked": "vet checked",
    "microchipped": "microchipped",
    "registered": "registered",
}


@dataclass
class _SourceState:
    source: Source
    next_run: float = 0.0
    backoff: float = 0.0
    backfilled: bool = False
    last_ok: str | None = None
    last_problem: str = ""


class Finder:
    def __init__(self, s: Settings, colour_checker=None, telegram: Telegram | None = None, mailer: Mailer | None = None):
        self.s = s
        self.store = Store(s.db_path)
        self.http = PoliteClient(s.user_agent, s.request_delay_seconds)
        self.geo = Geocoder()
        self.home = self.geo.lookup(s.home_postcode) if s.home_postcode else None
        if s.home_postcode and not self.home:
            log.warning("Couldn't find HOME_POSTCODE %r; distance limit is off", s.home_postcode)
        if colour_checker is None and s.anthropic_api_key:
            from .vision import ColourChecker

            colour_checker = ColourChecker(s, self.http)
        self.checker = colour_checker
        self.tg = telegram or (Telegram(s.telegram_token, s.telegram_chat_id) if s.telegram_token else None)
        self.mailer = mailer or Mailer(s)
        self.sources = [_SourceState(src) for src in build_sources(s.sources, self.http)]
        self._stop = threading.Event()
        self._code_event = threading.Event()
        self._code_value: str | None = None
        self.claude_problem: str = ""  # set while the photo check can't run
        self._recheck_after_price_change()
        # Adverts that failed before this version get one more chance.
        if self.store.meta_get("requeued_errors_v2") != "1":
            n = self.store.requeue("status='error'")
            self.store.meta_set("requeued_errors_v2", "1")
            if n:
                log.info("Giving %d adverts that failed earlier another check", n)
        # Sites where Yes sends the message through the site's own chat, logged in as you.
        self.chats: dict = {}
        if s.p4h_auto_message or s.preloved_auto_message:
            from .p4h_chat import P4HChat, PrelovedChat, materialise_session

            if s.p4h_auto_message:
                materialise_session(s.p4h_session_path, "P4H")
                self.chats["pets4homes"] = P4HChat(
                    s.p4h_session_path, s.p4h_email, s.p4h_password, s.chromium_path, ask_code=self._ask_code
                )
            if s.preloved_auto_message:
                materialise_session(s.preloved_session_path, "PRELOVED")
                self.chats["preloved"] = PrelovedChat(
                    s.preloved_session_path, s.preloved_email, s.preloved_password, s.chromium_path,
                    ask_code=self._ask_code,
                )

    # ------------------------------------------------------------------ scan
    @property
    def paused(self) -> bool:
        return self.store.meta_get("paused") == "1"

    def distance(self, l: Listing) -> float | None:
        return self.geo.distance_miles(self.home, l.lat, l.lon, l.location)

    def process(self, listing: Listing, src: Source) -> str:
        """Run one unseen listing through the pipeline. Returns the final status."""
        v = filters.prefilter(listing, self.s, self.distance(listing))
        if not v.ok:
            self.store.record(listing, "rejected", v.reason)
            return "rejected"
        if not listing.enriched:
            listing = src.enrich(listing)
            v = filters.prefilter(listing, self.s, self.distance(listing))
            if not v.ok:
                self.store.record(listing, "rejected", v.reason)
                return "rejected"
        fallback = False
        if self.checker is not None:
            from .vision import ClaudeBusy, ClaudeUnavailable

            try:
                assessment, used_images = self.checker.assess(listing)
                if self.claude_problem:
                    self._claude_recovered()
            except ClaudeUnavailable as e:
                # Don't go quiet: judge from the words for now, warn, and re-check
                # these adverts properly once the photo check works again.
                self._claude_failed(str(e))
                assessment, used_images = filters.text_only_assessment(listing), listing.images[:1]
                fallback = True
            except ClaudeBusy as e:
                log.warning("Photo check busy (%s); will retry %s next scan", e, listing.url)
                raise _ClaudeBusyStop(str(e)) from e
        else:
            assessment, used_images = filters.text_only_assessment(listing), listing.images[:1]
        v = filters.final_check(listing, assessment, self.s)
        if not v.ok:
            reason = f"{v.reason} [{TEXT_ONLY_FALLBACK}]" if fallback else v.reason
            self.store.record(listing, "rejected", reason, assessment)
            log.info("Rejected after photo check (%s): %s", v.reason, listing.url)
            return "rejected"
        msg_id = self.send_alert(listing, assessment, used_images)
        self.store.record(listing, "notified", "", assessment, telegram_message_id=msg_id)
        log.info("ALERT sent for %s", listing.url)
        return "notified"

    def scan_source(self, st: _SourceState) -> dict[str, int]:
        pages = self.s.pages_per_scan
        if not st.backfilled and self.store.meta_get(f"backfilled:{st.source.name}") != "1":
            pages = max(pages, self.s.backfill_pages)
        stats: dict[str, int] = {}
        for page in range(1, pages + 1):
            found = list(st.source.search(page))
            if not found:
                break
            for listing in found:
                if self._stop.is_set():
                    return stats
                if self.store.is_done(listing.key):
                    stats["seen"] = stats.get("seen", 0) + 1
                    continue
                try:
                    status = self.process(listing, st.source)
                except (BlockedError, _ClaudeBusyStop):
                    raise
                except Exception as e:  # noqa: BLE001 - one bad advert mustn't stop the scan
                    log.exception("Failed on %s", listing.url)
                    self.store.record(listing, "error", f"{type(e).__name__}: {e}"[:300])
                    status = "error"
                stats[status] = stats.get(status, 0) + 1
        st.backfilled = True
        self.store.meta_set(f"backfilled:{st.source.name}", "1")
        return stats

    def scan_due_sources(self) -> None:
        now = time.monotonic()
        for st in self.sources:
            if self._stop.is_set() or now < st.next_run:
                continue
            name = st.source.name
            try:
                stats = self.scan_source(st)
                st.backoff = 0
                st.last_ok = datetime.now(timezone.utc).isoformat(timespec="seconds")
                st.last_problem = ""
                self.store.meta_set(f"last_ok:{name}", st.last_ok)
                log.info("%s scan done: %s", name, stats or "nothing new")
                interval = self.s.scan_interval_seconds
                if stats.get("error", 0) >= 3:
                    row = self.store.last_error()
                    self._warn(
                        "errors",
                        f"⚠️ {stats['error']} adverts on {name} failed to process just now."
                        + (f"\nLatest: <code>{esc((row['reason'] or '')[:250])}</code>" if row else "")
                        + "\nThey'll be retried on the next scans.",
                    )
            except BlockedError as e:
                st.backoff = min(max(st.backoff * 2, 300), 3600)
                st.last_problem = f"site refusing us ({e})"
                log.warning("%s is blocking us (%s); backing off %ds", name, e, st.backoff)
                interval = st.backoff
                if st.backoff >= 3600:
                    self._warn(f"blocked:{name}", f"⚠️ {name} has been refusing the finder for over an hour ({esc(str(e))}). It keeps retrying hourly.")
            except _ClaudeBusyStop as e:
                st.last_problem = f"photo check busy ({e})"
                interval = self.s.scan_interval_seconds
            except Exception as e:  # noqa: BLE001
                log.exception("%s scan failed", name)
                st.last_problem = f"{type(e).__name__}: {e}"[:200]
                interval = self.s.scan_interval_seconds
            # Jitter so we don't hit sites on an exact beat.
            st.next_run = time.monotonic() + interval * random.uniform(0.85, 1.15)

    def run_scanner(self) -> None:
        while not self._stop.is_set():
            if not self.paused:
                self.scan_due_sources()
            try:
                self.maybe_daily_summary()
            except Exception:  # noqa: BLE001
                log.exception("daily summary failed")
            self._stop.wait(5)

    def _recheck_after_price_change(self) -> None:
        """If the price cap went up, adverts turned down only for price get another look."""
        cap = self.s.max_price_gbp
        old = self.store.meta_get("max_price_gbp")
        self.store.meta_set("max_price_gbp", str(cap))
        if old is None or float(old) >= cap:
            return
        keys = [
            r["key"]
            for r in self.store._db.execute(
                "SELECT key, reason FROM listings WHERE status='rejected' AND reason LIKE 'price £% over cap'"
            ).fetchall()
            if float(r["reason"].split("£")[1].split()[0].replace(",", "")) <= cap
        ]
        for k in keys:
            self.store.requeue("key=?", (k,))
        log.info("Price cap raised to £%.0f: re-checking %d adverts turned down for price", cap, len(keys))

    # ------------------------------------------------------------ health
    def _warn(self, key: str, text: str) -> None:
        """Tell the user about a problem, at most every few hours per kind of problem."""
        log.warning("%s", text)
        last = float(self.store.meta_get(f"warned:{key}") or 0)
        if time.time() - last < WARN_EVERY_SECONDS or self.tg is None:
            return
        try:
            self.tg.send_text(text)
            self.store.meta_set(f"warned:{key}", str(time.time()))
        except Exception as e:  # noqa: BLE001
            log.warning("couldn't send warning: %s", e)

    def _claude_failed(self, problem: str) -> None:
        self.claude_problem = problem
        self._warn(
            "claude",
            "⚠️ <b>The Claude photo check isn't working</b>\n"
            f"<code>{esc(problem)}</code>\n\n"
            "Until it's fixed I'm judging adverts from their words only, so I'll miss kittens whose "
            "colour only shows in the photos. Usual fixes: add credit at console.anthropic.com "
            "(Billing), or check ANTHROPIC_API_KEY in the .env file on the VPS, then rerun the installer.",
        )

    def _claude_recovered(self) -> None:
        self.claude_problem = ""
        n = self.store.requeue("status='rejected' AND reason LIKE ?", (f"%[{TEXT_ONLY_FALLBACK}]",))
        log.info("Photo check is working again; re-checking %d adverts", n)
        if self.tg and n:
            try:
                self.tg.send_text(f"✅ The Claude photo check is working again. Re-checking {n} adverts it missed.")
            except Exception:  # noqa: BLE001
                pass

    def photo_check_line(self) -> str:
        if self.checker is None:
            return "📷 Photo check: off (no Claude key), judging from the advert words only"
        if self.claude_problem:
            return f"📷 Photo check: ⚠️ failing ({esc(self.claude_problem[:150])}), using words only"
        return "📷 Photo check: Claude ✅"

    def maybe_daily_summary(self) -> None:
        """Once a day, say the finder is alive and what it looked at, so a quiet day
        is clearly 'no dark grey kittens' rather than 'something is broken'."""
        if not self.s.daily_summary or self.tg is None or not self.s.telegram_chat_id:
            return
        now = datetime.now(timezone.utc)
        if now.hour < self.s.daily_summary_hour_utc:
            return
        today = now.date().isoformat()
        if self.store.meta_get("daily_summary") == today:
            return
        self.store.meta_set("daily_summary", today)
        self.tg.send_text(self.summary_text(24, heading="🗓 <b>Daily check-in</b>"))

    def summary_text(self, hours: float, heading: str) -> str:
        since = _since(hours)
        st = self.store.stats_since(since)
        new = sum(st.values())
        alerts = sum(st.get(k, 0) for k in ("notified", "approved", "declined", "contacted"))
        lines = [heading, self.photo_check_line()]
        lines.append(f"Last {hours:.0f} h: {new} new adverts, {alerts} alert{'s' if alerts != 1 else ''}")
        reasons = self.store.top_reasons(since)
        if reasons:
            lines.append("Turned down: " + ", ".join(f"{esc(r)} {n}" for r, n in reasons))
        colours = self.store.colours_seen(since)
        if colours:
            lines.append("Colours seen: " + ", ".join(f"{esc(c)} ×{n}" for c, n in colours))
        if st.get("error"):
            row = self.store.last_error()
            lines.append(f"⚠️ {st['error']} failed" + (f", latest: <code>{esc((row['reason'] or '')[:200])}</code>" if row else ""))
        lines.append(f"Last alert: {_ago(self.store.last_alert_time())}")
        site_bits = []
        for s in self.sources:
            ok = s.last_ok or self.store.meta_get(f"last_ok:{s.source.name}")
            bit = f"{s.source.name} {_ago(ok)}"
            if s.last_problem:
                bit += f" ⚠️ {esc(s.last_problem[:80])}"
            site_bits.append(bit)
        lines.append("Last good scan: " + "; ".join(site_bits))
        return "\n".join(lines)

    # ----------------------------------------------------------------- alert
    def caption(self, l: Listing, a: Assessment) -> str:
        price = f"£{l.price_gbp:,.0f}" if l.price_gbp else "price not stated"
        if l.price_gbp and l.price_gbp <= self.s.preferred_price_gbp:
            price += " ⭐ under budget"
        age = ""
        days = l.age_days if l.age_days is not None else (int(a.age_weeks * 7) if a.age_weeks else None)
        if days is not None:
            age = f"{days // 7} weeks old" if days < 120 else f"{days // 30} months old"
        dist = self.distance(l)
        where = l.location or "location not stated"
        if dist is not None:
            where += f" ({dist:.0f} miles)"
        health = filters.health_facts(l, a)
        yes = [HEALTH_LABELS[k] for k, v in health.items() if v]
        no = [HEALTH_LABELS[k] for k, v in health.items() if v is False]
        lines = [
            f"🐈‍⬛ <b>{esc(l.title[:120])}</b>",
            f"💷 {esc(price)}" + (f" · 🎂 {age}" if age else ""),
            f"📍 {esc(where)}",
            f"🎨 {esc((a.colour or '')[:120])} (confidence {a.confidence:.0%})",
        ]
        if yes:
            lines.append("✅ " + esc(", ".join(yes)))
        if no:
            lines.append("⚠️ not: " + esc(", ".join(no)))
        if a.summary:
            summary = a.summary if len(a.summary) <= 350 else a.summary[:347].rsplit(" ", 1)[0] + "…"
            lines.append(f"\n{esc(summary)}")
        lines.append(f"\n{esc(l.source.title())}" + (f" · {esc(l.seller_type)}" if l.seller_type else ""))
        email = find_email(l)
        if email:
            lines.append("📧 Yes will email the seller")
        elif l.source in self.chats:
            lines.append(f"💬 Yes will message the seller on {self.chats[l.source].SITE} as you")
        else:
            lines.append("💬 Yes gives you a ready-to-paste message (site has no email)")
        return "\n".join(lines)

    def keyboard(self, l: Listing) -> list:
        return [
            [
                {"text": "✅ Yes, contact seller", "callback_data": f"y|{l.key}"[:64]},
                {"text": "❌ No", "callback_data": f"n|{l.key}"[:64]},
            ],
            [{"text": "🔗 Open advert", "url": l.url}],
        ]

    def send_alert(self, l: Listing, a: Assessment, used_images: list[str]) -> int:
        photo = None
        img_url = None
        if used_images:
            img_url = used_images[a.best_image_index if a.best_image_index < len(used_images) else 0]
        elif l.images:
            img_url = l.images[0]
        if img_url:
            try:
                photo, _ = self.http.get_bytes(img_url, max_bytes=9_000_000)
            except Exception:  # noqa: BLE001
                photo = None
        caption = self.caption(l, a)
        try:
            msg = self.tg.send_photo(photo, caption, self.keyboard(l), photo_url=None if photo else img_url)
        except TelegramError as e:
            # A photo Telegram won't take mustn't cost us the alert.
            log.warning("Photo alert failed (%s); sending it as text", e)
            msg = self.tg.send_text(caption + (f'\n<a href="{esc(img_url)}">📷 photo</a>' if img_url else ""),
                                    keyboard=self.keyboard(l))
        return msg["message_id"]

    # -------------------------------------------------------------- buttons
    def handle_callback(self, cq: dict) -> None:
        chat_id = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
        if chat_id != str(self.s.telegram_chat_id):
            self.tg.answer_callback(cq["id"], "Not authorised")
            return
        data = cq.get("data", "")
        action, _, key = data.partition("|")
        row = self.store.get(key)
        msg_id = (cq.get("message") or {}).get("message_id")
        if row is None:
            self.tg.answer_callback(cq["id"], "I don't know that advert any more")
            return
        listing = listing_from_json(row["data"])
        if action == "n":
            if self.store.set_status(key, "declined", expect="notified"):
                self.tg.answer_callback(cq["id"], "Skipped. Still looking!")
                self._mark(msg_id, listing, "❌ Skipped")
            else:
                self.tg.answer_callback(cq["id"], f"Already handled ({row['status']})")
            return
        if action == "y":
            # Atomic: only the first Yes on a 'notified' advert does anything.
            if not self.store.set_status(key, "approved", expect="notified"):
                self.tg.answer_callback(cq["id"], f"Already handled ({row['status']})")
                return
            self.tg.answer_callback(cq["id"], "On it…")
            self.contact_seller(listing, msg_id)

    def _mark(self, msg_id: int | None, l: Listing, label: str) -> None:
        if msg_id is None:
            return
        self.tg.edit_caption(
            msg_id,
            f"{label}\n<b>{esc(l.title)}</b>",
            keyboard=[[{"text": "🔗 Open advert", "url": l.url}]],
        )

    def contact_seller(self, l: Listing, msg_id: int | None) -> None:
        """Only ever called after the user tapped Yes (status moved to 'approved')."""
        row = self.store.get(l.key)
        if row is None or row["status"] != "approved":
            raise PermissionError("refusing to contact a seller without an explicit Yes")
        subject, body = render_message(l, self.s)
        email = find_email(l)
        phone = find_phone(l)
        src = next((st.source for st in self.sources if st.source.name == l.source), None)
        if email:
            try:
                self.mailer.send(email, subject, body)
            except Exception as e:  # noqa: BLE001
                log.exception("email failed")
                self.store.set_status(l.key, "notified", f"email failed: {e}")
                self.tg.send_text(
                    f"⚠️ Couldn't send the email to {esc(email)}: {esc(str(e))}\n"
                    "Tap Yes again to retry, or use the message below yourself.\n\n"
                    f"<pre>{esc(body)}</pre>",
                    reply_to=msg_id,
                )
                return
            self.store.set_status(l.key, "contacted", f"emailed {email}")
            self._mark(msg_id, l, f"✅ Emailed {esc(email)}" + (" (dry run)" if self.s.dry_run else ""))
            extra = f"\n📞 Their number from the advert: {esc(phone)}" if phone else ""
            self.tg.send_text(f"✅ Email sent to {esc(email)}.{extra}", reply_to=msg_id)
            return
        if l.source in self.chats:
            # Runs in its own thread: it may need to ask you for a login code over Telegram,
            # and the Telegram thread has to stay free to receive your reply.
            threading.Thread(
                target=self._site_message, args=(l, body, phone, msg_id), name="site-chat", daemon=True
            ).start()
            return
        self._manual_contact(l, body, phone, msg_id, src)

    def _site_message(self, l: Listing, body: str, phone: str | None, msg_id: int | None) -> None:
        chat = self.chats[l.source]
        site = chat.SITE
        try:
            res = chat.send(l.url, body)
        except Exception as e:  # noqa: BLE001
            log.exception("%s chat failed", site)
            from .p4h_chat import ChatResult

            res = ChatResult(False, str(e)[:300])
        if res.ok:
            self.store.set_status(l.key, "contacted", f"{l.source} chat")
            self._mark(msg_id, l, f"✅ Messaged the seller on {site}")
            extra = f"\n📞 Their number from the advert: {esc(phone)}" if phone else ""
            if res.screenshot:
                self.tg.send_photo(res.screenshot, f"✅ Sent on {site} as you.{extra}")
            else:
                self.tg.send_text(f"✅ Sent on {site} as you.{extra}", reply_to=msg_id)
            return
        if res.screenshot:
            self.tg.send_photo(res.screenshot, f"⚠️ {site} message didn't work: {esc(res.detail)}")
        src = next((st.source for st in self.sources if st.source.name == l.source), None)
        self._manual_contact(l, body, phone, msg_id, src, problem=res.detail)

    def _ask_code(self, prompt: str, timeout: float = 600) -> str | None:
        self._code_value = None
        self._code_event.clear()
        self.tg.send_text(f"🔐 {esc(prompt)}")
        if self._code_event.wait(timeout):
            return self._code_value
        return None

    def _manual_contact(self, l: Listing, body: str, phone: str | None, msg_id: int | None, src, problem: str = "") -> None:
        # No email on the advert: hand the user a ready message for the site's chat.
        note = src.contact_note if src else "This site has no email address for the seller."
        lines = [f"💬 {esc(note)}"]
        if problem:
            lines.insert(0, f"⚠️ Automatic message failed ({esc(problem)}), so here it is to send yourself.")
        if phone:
            lines.append(f"📞 The advert lists a phone number: <b>{esc(phone)}</b> (calling is fastest)")
        lines.append(f"\n<pre>{esc(body)}</pre>")
        self.store.set_status(l.key, "contacted", "manual: site messaging")
        self._mark(msg_id, l, "✅ Yes: message ready below")
        self.tg.send_text(
            "\n".join(lines),
            reply_to=msg_id,
            keyboard=[[{"text": "🔗 Open advert to send", "url": l.url}]],
        )

    def handle_message(self, msg: dict) -> None:
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not self.s.telegram_chat_id:
            # Setup helper: tell whoever messages the bot their chat id.
            self.tg.send_text(
                f"Your chat id is <code>{esc(chat_id)}</code>. Put it in TELEGRAM_CHAT_ID and restart.",
                chat_id=chat_id,
            )
            log.warning("Telegram chat id for %s is %s", (msg.get("from") or {}).get("first_name"), chat_id)
            return
        if chat_id != str(self.s.telegram_chat_id):
            return
        cmd = text.split()[0].split("@")[0].lower() if text else ""
        if cmd in ("/start", "/status"):
            c = self.store.counts()
            self.tg.send_text(
                "🔎 Maine Coon finder is running"
                + (" (⏸ paused)" if self.paused else "")
                + f"\nSites: {', '.join(st.source.name for st in self.sources)}"
                + f"\nChecked: {sum(c.values())} adverts"
                + f"\nAlerts sent: {c.get('notified', 0) + c.get('approved', 0) + c.get('declined', 0) + c.get('contacted', 0)}"
                + f"\nContacted: {c.get('contacted', 0)} · Skipped: {c.get('declined', 0)}"
                + f"\nRules: dark grey only, under {self.s.max_age_days // 30} months, max £{self.s.max_price_gbp:.0f}"
                + (f", within {self.s.max_distance_miles:.0f} miles" if self.s.max_distance_miles and self.home else "")
                + "\n\n" + self.summary_text(24, heading="<b>Last 24 hours</b>")
                + "\n\nTurned down in the last 7 days: "
                + (", ".join(f"{esc(r)} {n}" for r, n in self.store.top_reasons(_since(168), 8)) or "none")
            )
        elif cmd == "/pause":
            self.store.meta_set("paused", "1")
            self.tg.send_text("⏸ Paused: I've stopped checking sites. /resume to start again.")
        elif cmd == "/resume":
            self.store.meta_set("paused", "0")
            self.tg.send_text("▶️ Alerts are back on.")
        elif cmd == "/code":
            parts = text.split()
            if len(parts) >= 2:
                self._code_value = "".join(ch for ch in parts[1] if ch.isalnum())
                self._code_event.set()
                self.tg.send_text("👍 Got the code, logging in…")
        elif cmd == "/test":
            self.send_test_alert()

    def send_test_alert(self) -> None:
        l = Listing(
            source="test",
            source_id=str(int(time.time())),
            url="https://www.pets4homes.co.uk/sale/kittens/maine-coon/",
            title="TEST: Blue smoke Maine Coon boy (not a real advert)",
            description="This is a test alert. Tapping Yes shows the message, but sends no email.",
            price_gbp=450,
            location="Test town",
            age_days=70,
            health={"vaccinated": True, "wormed": True, "vet_checked": True},
        )
        a = Assessment(True, "blue smoke", 0.9, True, False, "test", summary="A test alert so you can try the buttons.")
        msg = self.tg.send_text(self.caption(l, a), keyboard=self.keyboard(l))
        self.store.record(l, "notified", "test", a, telegram_message_id=msg["message_id"])

    def run_telegram(self) -> None:
        if self.tg is None:
            log.warning("TELEGRAM_BOT_TOKEN not set: no alerts will be sent")
            return
        self.tg.set_commands()
        offset = None
        while not self._stop.is_set():
            try:
                updates = self.tg.get_updates(offset, timeout=50)
            except (TelegramError, Exception) as e:  # noqa: BLE001
                log.warning("Telegram polling error: %s", e)
                self._stop.wait(10)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    if "callback_query" in u:
                        self.handle_callback(u["callback_query"])
                    elif "message" in u:
                        self.handle_message(u["message"])
                except Exception:  # noqa: BLE001
                    log.exception("Failed handling Telegram update")

    # ------------------------------------------------------------------ main
    def run_forever(self) -> None:
        missing = self.s.missing_for_alerts()
        t = threading.Thread(target=self.run_telegram, name="telegram", daemon=True)
        t.start()
        try:
            if missing:
                # Don't scan (and mark adverts as seen) until alerts can actually go out.
                log.error(
                    "Not set up yet, missing: %s. Scanning is off until these are set. "
                    "(If only TELEGRAM_CHAT_ID is missing, message your bot and it will reply with it.)",
                    ", ".join(missing),
                )
                while not self._stop.is_set():
                    self._stop.wait(60)
                return
            self.run_scanner()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()

    def stop(self) -> None:
        self._stop.set()
