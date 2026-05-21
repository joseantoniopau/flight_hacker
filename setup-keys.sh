#!/usr/bin/env bash
# setup-keys.sh — interactive secrets setup. Writes to /Users/admin/Desktop/flight-hacker/.env
# Re-runs safely. Existing values are preserved unless you enter new ones.

set -e
ENV="/Users/admin/Desktop/flight-hacker/.env"

echo "=================================================================="
echo " FLIGHT-HACKER · setup-keys"
echo "=================================================================="
echo ""
echo " .env: $ENV"
echo ""

touch "$ENV"

prompt() {
  local key=$1
  local desc=$2
  local current
  current=$(grep -E "^${key}=" "$ENV" 2>/dev/null | cut -d= -f2- || echo "")
  if [ -n "$current" ]; then
    masked="${current:0:6}****"
    printf "  %s [%s]: " "$desc" "$masked"
  else
    printf "  %s: " "$desc"
  fi
  read -r val
  if [ -n "$val" ]; then
    if grep -qE "^${key}=" "$ENV"; then
      # macOS sed needs '' after -i
      sed -i.bak "s|^${key}=.*|${key}=${val}|" "$ENV" && rm "${ENV}.bak"
    else
      echo "${key}=${val}" >> "$ENV"
    fi
    echo "    set."
  else
    echo "    (kept)"
  fi
}

prompt SEATS_AERO_API_KEY        "Seats.aero Pro key (pro_xxxx)"
prompt TELEGRAM_WEBHOOK_URL      "Telegram webhook URL or Bot sendMessage URL"
prompt TELEGRAM_CHAT_ID          "Telegram chat id (for Bot sendMessage form)"
prompt AWARDWALLET_API_KEY       "AwardWallet API key (optional)"

# Duffel is B2B-only (travel agencies / OTAs). Personal users cannot realistically
# book through it. The code path stays available for anyone with an existing
# Duffel account who wants to wire it in as a second cash source — set
# DUFFEL_API_TOKEN in .env directly and search_cash.py will pick it up.
echo ""
echo "  Skipping Duffel: it's a B2B API for travel agencies, not personal users."
echo "  If you already have access, set DUFFEL_API_TOKEN in .env manually."

echo ""
echo " Wrote .env."
echo ""
