"""Command line entry point.

    python -m maine_coon_finder            # run forever (scanner + Telegram bot)
    python -m maine_coon_finder scan-once  # one pass over every site, then exit
    python -m maine_coon_finder preview    # list what each site returns right now (no Claude, no alerts)
    python -m maine_coon_finder test-alert # send a test alert to Telegram
    python -m maine_coon_finder p4h-login  # log in to Pets4Homes in a browser window, save the session
    python -m maine_coon_finder p4h-check  # check the saved Pets4Homes login still works
    python -m maine_coon_finder preloved-login / preloved-check  # same for Preloved
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maine_coon_finder")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "scan-once", "preview", "test-alert", "p4h-login", "p4h-check", "preloved-login", "preloved-check"])
    ap.add_argument("--env", default=".env", help="path to a .env file (default: .env)")
    args = ap.parse_args(argv)

    s = Settings.from_env(args.env)
    logging.basicConfig(
        level=getattr(logging, s.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "preview":
        from . import filters
        from .http import PoliteClient
        from .sources import build_sources

        http = PoliteClient(s.user_agent, s.request_delay_seconds)
        for src in build_sources(s.sources, http):
            try:
                found = list(src.search(1))
            except Exception as e:  # noqa: BLE001
                print(f"[{src.name}] FAILED: {e}")
                continue
            print(f"[{src.name}] {len(found)} adverts on page 1")
            for l in found:
                v = filters.prefilter(l, s, None)
                price = f"£{l.price_gbp:.0f}" if l.price_gbp else "-"
                print(f"   {'PASS' if v.ok else 'skip':4} {price:>6}  {l.title[:60]:60}  {v.reason}")
        return 0

    if args.command.endswith(("-login", "-check")) and args.command != "test-alert":
        from .p4h_chat import P4HChat, PrelovedChat, materialise_session

        if args.command.startswith("p4h"):
            materialise_session(s.p4h_session_path, "P4H")
            chat = P4HChat(s.p4h_session_path, s.p4h_email, s.p4h_password, s.chromium_path)
        else:
            materialise_session(s.preloved_session_path, "PRELOVED")
            chat = PrelovedChat(s.preloved_session_path, s.preloved_email, s.preloved_password, s.chromium_path)
        if args.command.endswith("-login"):
            chat.interactive_login()
            return 0
        ok = chat.has_session and chat.check()
        print(f"{chat.SITE} login OK" if ok else f"{chat.SITE} login missing or expired: run {args.command[:-6]}-login")
        return 0 if ok else 1

    from .app import Finder

    finder = Finder(s)
    if args.command == "test-alert":
        if not finder.tg or not s.telegram_chat_id:
            print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
            return 1
        finder.send_test_alert()
        print("Test alert sent. Run the finder to handle the button taps.")
        return 0
    if args.command == "scan-once":
        missing = s.missing_for_alerts()
        if missing:
            print(f"Missing settings: {', '.join(missing)}")
            return 1
        for st in finder.sources:
            print(st.source.name, finder.scan_source(st))
        return 0
    finder.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
