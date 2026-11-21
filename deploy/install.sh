#!/usr/bin/env bash
# Install or update the Maine Coon finder on a Linux VPS (Ubuntu/Debian).
#
#   curl -fsSL https://raw.githubusercontent.com/sfxhewitt/Maine-Coon-Finder/main/deploy/install.sh | sudo bash
#
# or, from a checkout:   sudo bash deploy/install.sh
#
# First run asks for your settings (typed on the VPS, never sent anywhere else)
# and writes /opt/maine-coon-finder/.env. Re-running it pulls the latest code and
# restarts; your .env and data are kept.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sfxhewitt/Maine-Coon-Finder.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-/opt/maine-coon-finder}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo." >&2
  exit 1
fi

# 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker…"
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  say "Installing the Docker Compose plugin…"
  apt-get update -y && apt-get install -y docker-compose-plugin
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# 2. Code
command -v git >/dev/null 2>&1 || { apt-get update -y && apt-get install -y git; }
if [ -d "$DIR/.git" ]; then
  say "Updating code in $DIR…"
  git -C "$DIR" fetch --quiet origin "$BRANCH"
  git -C "$DIR" checkout --quiet "$BRANCH"
  git -C "$DIR" reset --quiet --hard "origin/$BRANCH"
else
  say "Downloading code to $DIR…"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$DIR"
fi
cd "$DIR"
mkdir -p data
chown 1000:1000 data 2>/dev/null || true

# 3. Settings (first run only)
ask() { # ask VAR "Question" [default] [secret]
  local var="$1" q="$2" def="${3:-}" secret="${4:-}" val
  if [ -n "$secret" ]; then
    read -r -s -p "$q: " val </dev/tty; echo
  else
    read -r -p "$q${def:+ [$def]}: " val </dev/tty
  fi
  val="${val:-$def}"
  printf '%s=%s\n' "$var" "$val" >> .env
}

if [ ! -f .env ]; then
  say "First-time setup: a few questions (press Enter to skip optional ones)."
  umask 077
  : > .env
  ask TELEGRAM_BOT_TOKEN "Telegram bot token (from @BotFather)" "" secret
  ask TELEGRAM_CHAT_ID "Telegram chat id"
  ask ANTHROPIC_API_KEY "Claude API key (optional: blank = text-only colour check)" "" secret
  ask SMTP_USER "Gmail address to send from (optional)"
  ask SMTP_PASSWORD "Gmail app password (optional)" "" secret
  ask BUYER_NAME "Your name (signs the message)"
  ask BUYER_PHONE "Your phone number (optional)"
  ask HOME_POSTCODE "Your postcode (optional: blank = whole UK)"
  ask MAX_DISTANCE_MILES "Max distance in miles (optional)"
  ask MAX_PRICE_GBP "Max price £" "1500"
  ask P4H_AUTO_MESSAGE "Send the Pets4Homes chat message as you on Yes? (true/false)" "false"
  ask P4H_EMAIL "Pets4Homes login email (only if the line above is true)"
  ask PRELOVED_AUTO_MESSAGE "Send the Preloved reply as you on Yes? (true/false)" "false"
  ask PRELOVED_EMAIL "Preloved login email (only if the line above is true)"
  ask PRELOVED_PASSWORD "Preloved password (only if the line above is true)" "" secret
  cat >> .env <<'EOF'
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
PREFERRED_PRICE_GBP=500
SCAN_INTERVAL_SECONDS=120
SOURCES=pets4homes,gumtree,preloved
EOF
  chmod 600 .env
  echo "Saved settings to $DIR/.env (edit it any time, then: sudo bash deploy/install.sh)"
fi

# 4. Run
say "Building and starting…"
docker compose up -d --build
docker image prune -f >/dev/null 2>&1 || true

say "Done. The finder is running and restarts by itself after reboots."
echo "  Logs:    cd $DIR && docker compose logs -f"
echo "  Status:  send /status to your Telegram bot"
echo "  Update:  sudo bash $DIR/deploy/install.sh"
if grep -q '^P4H_AUTO_MESSAGE=true' .env; then
  echo "  Pets4Homes: the bot will ask you on Telegram for the login code Pets4Homes emails you (reply /code 123456)."
fi
