"""Settings, all read from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the app has no extra dependency for it."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _float(name: str, default: float | None) -> float | None:
    v = _get(name)
    if not v:
        return default
    return float(v)


def _int(name: str, default: int) -> int:
    v = _get(name)
    return int(v) if v else default


def _bool(name: str, default: bool) -> bool:
    v = _get(name).lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def _list(name: str, default: list[str]) -> list[str]:
    v = _get(name)
    if not v:
        return default
    return [x.strip().lower() for x in v.split(",") if x.strip()]


@dataclass
class Settings:
    # --- Telegram ---
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # --- Claude (photo colour check) ---
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    max_images_per_check: int = 4

    # --- Email (SMTP) ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""  # defaults to smtp_user
    email_from_name: str = ""
    email_bcc_self: bool = True

    # --- Who you are (goes into the message to the seller) ---
    buyer_name: str = ""
    buyer_phone: str = ""
    buyer_location: str = ""  # e.g. "Leeds" - mentioned in the message
    email_template_path: str = "templates/seller_message.txt"

    # --- Search rules ---
    max_price_gbp: float = 1000.0  # hard cap
    preferred_price_gbp: float = 500.0  # nice-to-have
    max_age_days: int = 365  # hard rule: under 1 year
    home_postcode: str = ""  # e.g. "LS1 4AP"; blank = whole UK
    max_distance_miles: float | None = None  # blank = no limit
    allow_crossbreeds: bool = False
    min_colour_confidence: float = 0.6
    # Colours that count as "dark grey" for this search.
    accepted_colours: list[str] = field(
        default_factory=lambda: [
            "solid blue",
            "blue smoke",
            "black smoke",
            "dark grey",
            "charcoal",
        ]
    )
    sources: list[str] = field(default_factory=lambda: ["pets4homes", "gumtree", "preloved"])

    # --- Timing / politeness ---
    scan_interval_seconds: int = 180
    request_delay_seconds: float = 2.0
    backfill_pages: int = 3  # pages to read on the very first run
    pages_per_scan: int = 1
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )

    # --- Pets4Homes chat as you (optional, against P4H terms: your call) ---
    p4h_auto_message: bool = False
    p4h_email: str = ""
    p4h_password: str = ""
    p4h_session_path: str = "data/p4h_session.json"
    preloved_auto_message: bool = False
    preloved_email: str = ""
    preloved_password: str = ""
    preloved_session_path: str = "data/preloved_session.json"
    chromium_path: str = ""

    # --- Storage / modes ---
    db_path: str = "data/finder.sqlite3"
    dry_run: bool = False  # never actually send email when true
    log_level: str = "INFO"
    # A once-a-day "still alive" message with what was checked (hour is UTC).
    daily_summary: bool = True
    daily_summary_hour_utc: int = 9

    @classmethod
    def from_env(cls, dotenv: str | Path = ".env") -> "Settings":
        _load_dotenv(Path(dotenv))
        d = cls()
        return cls(
            telegram_token=_get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_get("TELEGRAM_CHAT_ID"),
            anthropic_api_key=_get("ANTHROPIC_API_KEY"),
            claude_model=_get("CLAUDE_MODEL", d.claude_model),
            max_images_per_check=_int("MAX_IMAGES_PER_CHECK", d.max_images_per_check),
            smtp_host=_get("SMTP_HOST", d.smtp_host),
            smtp_port=_int("SMTP_PORT", d.smtp_port),
            smtp_user=_get("SMTP_USER"),
            smtp_password=_get("SMTP_PASSWORD"),
            email_from=_get("EMAIL_FROM") or _get("SMTP_USER"),
            email_from_name=_get("EMAIL_FROM_NAME") or _get("BUYER_NAME"),
            email_bcc_self=_bool("EMAIL_BCC_SELF", d.email_bcc_self),
            buyer_name=_get("BUYER_NAME"),
            buyer_phone=_get("BUYER_PHONE"),
            buyer_location=_get("BUYER_LOCATION"),
            email_template_path=_get("EMAIL_TEMPLATE_PATH", d.email_template_path),
            max_price_gbp=_float("MAX_PRICE_GBP", d.max_price_gbp),
            preferred_price_gbp=_float("PREFERRED_PRICE_GBP", d.preferred_price_gbp),
            max_age_days=_int("MAX_AGE_DAYS", d.max_age_days),
            home_postcode=_get("HOME_POSTCODE"),
            max_distance_miles=_float("MAX_DISTANCE_MILES", None),
            allow_crossbreeds=_bool("ALLOW_CROSSBREEDS", d.allow_crossbreeds),
            min_colour_confidence=_float("MIN_COLOUR_CONFIDENCE", d.min_colour_confidence),
            accepted_colours=_list("ACCEPTED_COLOURS", d.accepted_colours),
            sources=_list("SOURCES", d.sources),
            scan_interval_seconds=_int("SCAN_INTERVAL_SECONDS", d.scan_interval_seconds),
            request_delay_seconds=_float("REQUEST_DELAY_SECONDS", d.request_delay_seconds),
            backfill_pages=_int("BACKFILL_PAGES", d.backfill_pages),
            pages_per_scan=_int("PAGES_PER_SCAN", d.pages_per_scan),
            user_agent=_get("USER_AGENT", d.user_agent),
            p4h_auto_message=_bool("P4H_AUTO_MESSAGE", d.p4h_auto_message),
            p4h_email=_get("P4H_EMAIL"),
            p4h_password=_get("P4H_PASSWORD"),
            p4h_session_path=_get("P4H_SESSION_PATH", d.p4h_session_path),
            preloved_auto_message=_bool("PRELOVED_AUTO_MESSAGE", d.preloved_auto_message),
            preloved_email=_get("PRELOVED_EMAIL"),
            preloved_password=_get("PRELOVED_PASSWORD"),
            preloved_session_path=_get("PRELOVED_SESSION_PATH", d.preloved_session_path),
            chromium_path=_get("CHROMIUM_PATH"),
            db_path=_get("DB_PATH", d.db_path),
            dry_run=_bool("DRY_RUN", d.dry_run),
            log_level=_get("LOG_LEVEL", d.log_level),
            daily_summary=_bool("DAILY_SUMMARY", d.daily_summary),
            daily_summary_hour_utc=_int("DAILY_SUMMARY_HOUR_UTC", d.daily_summary_hour_utc),
        )

    def missing_for_alerts(self) -> list[str]:
        out = []
        if not self.telegram_token:
            out.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_chat_id:
            out.append("TELEGRAM_CHAT_ID")
        return out

    def missing_for_email(self) -> list[str]:
        out = []
        for name, val in [
            ("SMTP_USER", self.smtp_user),
            ("SMTP_PASSWORD", self.smtp_password),
            ("BUYER_NAME", self.buyer_name),
        ]:
            if not val:
                out.append(name)
        return out
