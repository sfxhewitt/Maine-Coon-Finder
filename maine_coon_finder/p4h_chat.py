"""Optional: send the purchase message through Pets4Homes chat, logged in as you.

Pets4Homes has no seller email addresses, so the only way to contact a seller
is its chat. This drives a real (headless) browser with your saved login:
open advert -> press Message -> type the message -> send.

WARNING: Pets4Homes' terms don't allow automated use of accounts. You chose to
accept the risk that the account (and any Premium you've paid for) could be
suspended. The bot only ever sends ONE message per advert, and only after you
tap Yes.

Login is saved in a Playwright "storage state" file (cookies + local storage).
Ways to create it:
  * `python -m maine_coon_finder p4h-login` on your own computer (opens a
    browser window; you log in normally, including any code they email you),
  * or the bot logs in itself with P4H_EMAIL (+ P4H_PASSWORD if your account
    has one) and asks you on Telegram for the code Pets4Homes emails you.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

BASE = "https://www.pets4homes.co.uk"
LOGIN_URL = BASE + "/login/"

# Selectors. Pets4Homes renames things now and then, so each is a list of fallbacks.
CONSENT = ["#didomi-notice-agree-button", "button:has-text('Agree')", "button:has-text('Accept')"]
MESSAGE_BUTTON = ["[data-testid='chat-message-button']", "button:has-text('Message')"]
EMAIL_INPUT = ["[data-testid='new-login-email-input']", "input[name='email']", "input[type='email']"]
CONTINUE_BUTTON = ["[data-testid='new-login-continue-button']", "button[type='submit']"]
PASSWORD_INPUT = ["input[type='password']"]
CODE_INPUT = [
    "input[autocomplete='one-time-code']",
    "input[inputmode='numeric']",
    "input[name*='code' i]",
    "input[name*='otp' i]",
]
COMPOSER = [
    "textarea.str-chat__textarea__textarea",
    ".str-chat__message-input textarea",
    "[data-testid*='message-input' i] textarea",
    "textarea[placeholder*='message' i]",
    "textarea",
    "[contenteditable='true']",
]
SEND_BUTTON = [
    "button[aria-label*='send' i]",
    "[data-testid*='send' i]",
    ".str-chat__send-button",
    "button:has-text('Send')",
]


class P4HChatError(RuntimeError):
    pass


class NotLoggedIn(P4HChatError):
    pass


@dataclass
class ChatResult:
    ok: bool
    detail: str
    screenshot: bytes | None = None


def _first(page, selectors: list[str], timeout_ms: int = 0):
    """Return the first visible element matching any selector, waiting up to timeout."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001 - bad selector on this page version
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.3)


def _dismiss_consent(page) -> None:
    btn = _first(page, CONSENT, 3000)
    if btn:
        try:
            btn.click(timeout=3000)
        except Exception:  # noqa: BLE001
            pass


def _is_login_page(page) -> bool:
    return "/login" in page.url or "/signup" in page.url


class P4HChat:
    def __init__(
        self,
        session_path: str,
        email: str = "",
        password: str = "",
        chromium_path: str = "",
        ask_code: Callable[[str], str | None] | None = None,
    ):
        self.session_path = Path(session_path)
        self.email = email
        self.password = password
        self.chromium_path = chromium_path
        self.ask_code = ask_code  # asks the user (Telegram) for the emailed code
        self._lock = threading.Lock()  # one browser at a time

    # ----------------------------------------------------------- helpers
    @property
    def has_session(self) -> bool:
        return self.session_path.is_file()

    def _launch(self, p, headless: bool = True):
        kw = {"headless": headless}
        path = self.chromium_path or os.environ.get("CHROMIUM_PATH", "")
        if path:
            kw["executable_path"] = path
        return p.chromium.launch(**kw)

    def _context(self, browser):
        kw = {
            "viewport": {"width": 1280, "height": 900},
            "locale": "en-GB",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
        }
        if self.has_session:
            kw["storage_state"] = str(self.session_path)
        return browser.new_context(**kw)

    def _save(self, ctx) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(self.session_path))
        try:
            os.chmod(self.session_path, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------- login
    def login_headless(self, page) -> None:
        """Log in through the normal form. Needs P4H_EMAIL; asks on Telegram for any code."""
        if not self.email:
            raise NotLoggedIn("Pets4Homes login has expired and P4H_EMAIL isn't set")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        _dismiss_consent(page)
        box = _first(page, EMAIL_INPUT, 15000)
        if not box:
            raise P4HChatError("couldn't find the email box on the login page")
        box.fill(self.email)
        btn = _first(page, CONTINUE_BUTTON, 5000)
        if btn:
            btn.click()
        else:
            box.press("Enter")
        # Next screen is either a password box or a code box.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if not _is_login_page(page):
                return
            pw = _first(page, PASSWORD_INPUT)
            if pw and self.password:
                pw.fill(self.password)
                pw.press("Enter")
                page.wait_for_timeout(4000)
                if not _is_login_page(page):
                    return
            code_box = _first(page, CODE_INPUT)
            if code_box:
                if not self.ask_code:
                    raise NotLoggedIn("Pets4Homes wants an emailed code and there's no way to ask you for it")
                code = self.ask_code("Pets4Homes has emailed you a login code. Reply here with /code 123456")
                if not code:
                    raise NotLoggedIn("no login code received in time")
                boxes = page.locator(CODE_INPUT[0] + ", " + CODE_INPUT[1])
                n = boxes.count()
                if n > 1 and n >= len(code):  # one box per digit
                    for i, ch in enumerate(code):
                        boxes.nth(i).fill(ch)
                else:
                    code_box.fill(code)
                code_box.press("Enter")
                page.wait_for_timeout(5000)
                if not _is_login_page(page):
                    return
                raise NotLoggedIn("the login code didn't work")
            page.wait_for_timeout(500)
        if _is_login_page(page):
            raise NotLoggedIn("couldn't get past the Pets4Homes login page")

    def interactive_login(self) -> None:
        """Open a visible browser on your own computer to log in by hand."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = self._launch(p, headless=False)
            ctx = self._context(browser)
            page = ctx.new_page()
            page.goto(LOGIN_URL)
            input("Log in to Pets4Homes in the browser window, then press Enter here... ")
            self._save(ctx)
            browser.close()
        print(f"Saved login to {self.session_path}")
        print("For a server, set this as the P4H_SESSION_B64 secret:")
        print(base64.b64encode(self.session_path.read_bytes()).decode())

    def check(self) -> bool:
        """True if the saved login still works (opens your chat inbox)."""
        from playwright.sync_api import sync_playwright

        with self._lock, sync_playwright() as p:
            browser = self._launch(p)
            ctx = self._context(browser)
            page = ctx.new_page()
            try:
                page.goto(BASE + "/chat/", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                ok = not _is_login_page(page)
                if ok:
                    self._save(ctx)
                return ok
            finally:
                browser.close()

    # --------------------------------------------------------------- send
    def send(self, advert_url: str, text: str) -> ChatResult:
        from playwright.sync_api import sync_playwright

        with self._lock, sync_playwright() as p:
            browser = self._launch(p)
            ctx = self._context(browser)
            page = ctx.new_page()
            shot = None
            try:
                page.goto(BASE + "/", wait_until="domcontentloaded")
                _dismiss_consent(page)
                page.goto(advert_url, wait_until="domcontentloaded")
                _dismiss_consent(page)
                btn = _first(page, MESSAGE_BUTTON, 15000)
                if not btn:
                    raise P4HChatError("no Message button on the advert (sold, removed, or page changed)")
                btn.click()
                page.wait_for_timeout(3000)
                if _is_login_page(page):
                    self.login_headless(page)
                    self._save(ctx)
                    page.goto(advert_url, wait_until="domcontentloaded")
                    _dismiss_consent(page)
                    btn = _first(page, MESSAGE_BUTTON, 15000)
                    if not btn:
                        raise P4HChatError("no Message button after logging in")
                    btn.click()
                    page.wait_for_timeout(3000)
                box = _first(page, COMPOSER, 20000)
                if not box:
                    raise P4HChatError("the chat box didn't open (maybe Premium-only, or a litter picker needs choosing)")
                box.click()
                box.fill(text)
                send_btn = _first(page, SEND_BUTTON, 3000)
                if send_btn:
                    send_btn.click()
                else:
                    box.press("Enter")
                page.wait_for_timeout(3000)
                # Confirm: the first line of our message should now be in the conversation.
                first_line = next((ln for ln in text.splitlines() if len(ln.strip()) > 12), text)[:40]
                sent = page.get_by_text(first_line, exact=False).count() > 0
                composer_now = _first(page, COMPOSER)
                still_in_box = False
                if composer_now:
                    try:
                        still_in_box = first_line in (composer_now.input_value() or "")
                    except Exception:  # noqa: BLE001
                        still_in_box = False
                shot = page.screenshot(full_page=False)
                self._save(ctx)  # keep refreshed login tokens
                if sent and not still_in_box:
                    return ChatResult(True, "message sent in Pets4Homes chat", shot)
                return ChatResult(False, "couldn't confirm the message went through", shot)
            except P4HChatError as e:
                try:
                    shot = page.screenshot(full_page=False)
                except Exception:  # noqa: BLE001
                    shot = None
                return ChatResult(False, str(e), shot)
            finally:
                browser.close()


def materialise_session(session_path: str) -> None:
    """On a server, write the session file from the P4H_SESSION_B64 secret once."""
    b64 = os.environ.get("P4H_SESSION_B64", "").strip()
    path = Path(session_path)
    if b64 and not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(b64))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
