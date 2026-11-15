"""Send the purchase-interest email over SMTP (Gmail app password works)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import Settings

log = logging.getLogger(__name__)


class Mailer:
    def __init__(self, s: Settings):
        self.s = s

    def send(self, to: str, subject: str, body: str) -> str:
        """Send one email. Returns the Message-ID. Raises on failure."""
        s = self.s
        missing = s.missing_for_email()
        if missing:
            raise RuntimeError(f"Email isn't set up yet (missing {', '.join(missing)})")
        msg = EmailMessage()
        msg["From"] = formataddr((s.email_from_name or s.buyer_name, s.email_from))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Reply-To"] = s.email_from
        msg["Message-ID"] = make_msgid(domain=s.email_from.split("@")[-1])
        if s.email_bcc_self:
            msg["Bcc"] = s.email_from
        msg.set_content(body)
        if s.dry_run:
            log.warning("DRY_RUN: would email %s:\n%s", to, body)
            return msg["Message-ID"]
        ctx = ssl.create_default_context()
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=30) as smtp:
                smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ctx)
                smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        log.info("Emailed %s about %s", to, subject)
        return msg["Message-ID"]
