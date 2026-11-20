# Maine Coon Finder

Watches UK pet sites for **dark grey Maine Coon kittens** and pings you on Telegram the moment one appears, with a photo, price, age, distance and health checks. Tap **Yes** and it contacts the seller for you; tap **No** and it moves on.

## How it works

```
every ~3 min, per site
  Pets4Homes / Gumtree / Preloved  ──▶  new advert?  (SQLite remembers everything already seen)
        │
        ▼
  free checks: Maine Coon? pedigree? not a cross? under 1 year? ≤ £1500? within distance? not sold?
        │
        ▼
  Claude looks at up to 4 photos + the text: is there a DARK GREY kitten?
  (solid blue, blue smoke, black smoke/charcoal; not silver, black, tabby, cream…)
  and pulls out vaccinated / wormed / vet checked / microchipped / registered
        │
        ▼
  Telegram alert:  photo · price (⭐ if ≤ £500) · age · miles away · health · [✅ Yes] [❌ No] [🔗 Open]
        │
   Yes ─┴─▶ email address in the advert?  ── yes ─▶ email sent from your account (BCC to you)
                                          └─ no ──▶ ready-to-paste message + link (site's own chat)
   No  ───▶ marked skipped, never shown again
```

**Nothing is ever sent to a seller without your Yes.** The code only contacts a seller after the advert's status changes from `notified` to `approved` via your button, and a double-tap can't send twice (both are covered by tests).

### Sites

| Site | Status | How we read it | Contacting the seller |
|---|---|---|---|
| Pets4Homes | ✅ | Search page JSON (newest first, kittens ≤ 12 months) | Pets4Homes chat only (login) |
| Gumtree | ✅ | Category pages + advert JSON-LD (robots.txt blocks `/search`, so we don't use it) | Gumtree message form |
| Preloved | ✅ | The JSON feed the site's own page uses | Preloved reply form |
| Freeads | ❌ | Blocks automated access (403). We don't try to get round it. | |
| Facebook | ❌ | Needs a login; scraping breaks Meta's terms | |
| Kittenlist | ❌ | Site returns an error page | |

**Important limitation:** UK classified sites hide sellers' email addresses. Yes sends an email only when the seller put an address in the advert text. Otherwise you get the message ready to paste into the site's chat, plus the seller's phone number if they listed one.

**Optional: Pets4Homes chat as you.** Set `P4H_AUTO_MESSAGE=true` and Yes on a Pets4Homes alert opens a headless browser with your saved login, presses *Message* on the advert, and sends your message. You get a screenshot back on Telegram. If anything goes wrong, you get the paste-ready message instead. ⚠️ Pets4Homes' terms don't allow automated use of accounts, so your account (and any Premium you paid for) could be suspended. It sends at most one message per advert, and only after your Yes.
Login: either run `python -m maine_coon_finder p4h-login` on your computer (a browser opens and you log in normally; it prints a `P4H_SESSION_B64` value to use as a server secret), or set `P4H_EMAIL` (plus `P4H_PASSWORD` if your account has one) and the bot logs in on its own, asking you on Telegram (`/code 123456`) for the code Pets4Homes emails you. `p4h-check` tells you if the saved login still works.

**Preloved works the same way:** set `PRELOVED_AUTO_MESSAGE=true`, `PRELOVED_EMAIL` and `PRELOVED_PASSWORD`, and Yes fills in the advert's *Contact seller* form for you. **Gumtree can't do this:** its login page blocks automated browsers, so Gumtree alerts always give you the paste-ready message.

Each site gets 1 search request per scan, plus one request per *new* advert, with at least 2 seconds between requests to the same site. If a site starts refusing, the finder backs off (5 min, doubling to 1 h) on its own.

## Setup

### 1. Telegram bot
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message.
3. Start the finder with only the token set: the bot replies with your chat id. Put it in `TELEGRAM_CHAT_ID` and restart.

### 2. Claude API key (recommended, optional)
Create a key at <https://console.anthropic.com> → `ANTHROPIC_API_KEY`. Only adverts that pass the free checks get a photo check, so cost is small (roughly 2-3p per advert checked; the first run checks the backlog once).

Without a key the finder still runs in **text-only mode**: it alerts only when the advert's words say blue / grey / charcoal / black smoke about the kittens (not just a parent). That misses adverts where the colour only shows in the photos, and alerts say "photo not checked".

### 3. Email (for the Yes button)
With Gmail: turn on 2-step verification, create an **app password** at <https://myaccount.google.com/apppasswords>, and set `SMTP_USER` (your address) and `SMTP_PASSWORD` (the app password). Set `BUYER_NAME` and `BUYER_PHONE`. Edit the wording in [`templates/seller_message.txt`](templates/seller_message.txt).

### 4. Your search
`HOME_POSTCODE` + `MAX_DISTANCE_MILES` limit by distance (blank = whole UK). Prices: `MAX_PRICE_GBP` (hard cap, default 1500) and `PREFERRED_PRICE_GBP` (gets a ⭐, default 500). Only pedigree kittens are shown (GCCF/TICA/FIFe registered or pedigree papers mentioned; set `REQUIRE_PEDIGREE=false` to drop that). All settings are in [`.env.example`](.env.example).

## Running it

### Option A: your VPS (recommended)
On the VPS (Ubuntu/Debian), run:
```bash
curl -fsSL https://raw.githubusercontent.com/sfxhewitt/Maine-Coon-Finder/main/deploy/install.sh | sudo bash
```
It installs Docker if needed, downloads the code to `/opt/maine-coon-finder`, asks for your settings (typed on the VPS, saved to `.env` with owner-only permissions), and starts the finder with `docker compose`. It restarts on its own after crashes and reboots. Run the same command again to update; your settings and data are kept.

### Option B: your own PC or a Raspberry Pi
```bash
cp .env.example .env   # fill it in
docker compose up -d   # restarts by itself after reboots
docker compose logs -f
```
A home connection is less likely to be blocked by sites, but it only works while the machine is on.

### Option C: plain Python
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill it in
python -m maine_coon_finder
```

### Why not GitHub Actions?
Scheduled workflows run at most every 5 minutes, are often delayed by 10-30 minutes at busy times, and can't receive your button taps between runs. For kittens that go within the hour, an always-on worker is the right tool.

## When it goes quiet

Every morning (09:00 UTC) the bot sends a short **daily check-in**: how many new adverts it looked at, why they were turned down, which colours the photo check saw, and when each site was last scanned. A quiet day then clearly means "no dark grey kittens" rather than "something broke". Turn it off with `DAILY_SUMMARY=false`.

If something does break, the bot tells you (at most every 6 hours per problem):
- **Claude photo check failing** (no credit, wrong key): it switches to judging adverts from their words so you still get alerts, and re-checks those adverts properly once Claude works again.
- **Adverts failing to process** or **a site refusing the finder** for over an hour.

## Commands

| Where | Command | What it does |
|---|---|---|
| Terminal | `python -m maine_coon_finder` | Run forever |
| Terminal | `python -m maine_coon_finder preview` | Show what each site returns right now and which adverts pass the free checks (no Claude, no alerts) |
| Terminal | `python -m maine_coon_finder scan-once` | One full pass, then exit |
| Terminal | `python -m maine_coon_finder test-alert` | Send a fake alert so you can see the buttons |
| Telegram | `/status` | Whether the photo check works, the last 24 h (new adverts, alerts, why adverts were turned down, colours seen, failures) and when each site was last scanned |
| Telegram | `/pause`, `/resume` | Stop/start checking sites |
| Telegram | `/test` | Fake alert (Yes on it never emails anyone) |
| Telegram | `/code 123456` | Give the bot a Pets4Homes login code when it asks |
| Terminal | `python -m maine_coon_finder p4h-login` | Log in to Pets4Homes once and save the session |

## Development
```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
Code: `maine_coon_finder/sources/` (one file per site), `filters.py` (rules), `vision.py` (Claude photo check), `app.py` (scan loop, alerts, buttons), `store.py` (SQLite). To add a site, subclass `Source` with `search()` and optionally `enrich()`, and register it in `sources/__init__.py`.
