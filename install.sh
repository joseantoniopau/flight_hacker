#!/usr/bin/env bash
# install.sh — install the flight-hacker skill end-to-end.
#
# Usage:  ./install.sh                      # full install
#         ./install.sh --skip-deps          # skip pip installs
#         ./install.sh --uninstall          # remove symlink + LaunchAgent
#
# This is idempotent — running it again refreshes symlinks and LaunchAgent.

set -e

ROOT="/Users/admin/Desktop/flight-hacker"
SKILLS_DIR="$HOME/.claude/skills"
SKILL_LINK="$SKILLS_DIR/flight-hacker"
PYTHON_BIN="$(command -v python3 || command -v python)"

SKIP_DEPS=0
UNINSTALL=0
for arg in "$@"; do
  case $arg in
    --skip-deps) SKIP_DEPS=1 ;;
    --uninstall) UNINSTALL=1 ;;
  esac
done

echo "=================================================================="
echo " FLIGHT-HACKER · install"
echo "=================================================================="
echo "root        : $ROOT"
echo "skills dir  : $SKILLS_DIR"
echo "python      : $PYTHON_BIN"
echo ""

if [ "$UNINSTALL" = "1" ]; then
  echo "[uninstall] removing symlink + LaunchAgent"
  [ -L "$SKILL_LINK" ] && rm "$SKILL_LINK" && echo "  symlink removed"
  if [ -f "$HOME/Library/LaunchAgents/com.fh.watcher.plist" ]; then
    launchctl unload "$HOME/Library/LaunchAgents/com.fh.watcher.plist" 2>/dev/null || true
    rm "$HOME/Library/LaunchAgents/com.fh.watcher.plist"
    echo "  LaunchAgent removed"
  fi
  echo "Done."
  exit 0
fi

# ----- 1. dependencies -----
if [ "$SKIP_DEPS" = "0" ]; then
  echo "[1/6] installing python deps"
  PIP_ARGS=""
  if [[ "$OSTYPE" == "darwin"* ]]; then
    PIP_ARGS="--break-system-packages"
  fi
  $PYTHON_BIN -m pip install --quiet --upgrade $PIP_ARGS \
    fast-flights \
    fastapi \
    uvicorn \
    || echo "  WARN: pip install had issues; retry with --break-system-packages if on macOS"
  echo "  ok"
else
  echo "[1/6] dep install skipped"
fi

# ----- 2. ensure dirs -----
echo "[2/6] ensuring directories"
mkdir -p "$ROOT/cache" "$ROOT/watches" "$SKILLS_DIR"
echo "  ok"

# ----- 3. .env -----
echo "[3/6] checking .env"
if [ ! -f "$ROOT/.env" ]; then
  cat > "$ROOT/.env" <<EOF
# flight-hacker secrets — gitignored.
SEATS_AERO_API_KEY=
# Duffel is B2B-only (travel agencies / OTAs). Skip unless you have access.
# DUFFEL_API_TOKEN=
# TELEGRAM_WEBHOOK_URL=
# TELEGRAM_CHAT_ID=
EOF
  echo "  created blank .env — run ./setup-keys.sh to populate"
else
  echo "  .env present"
fi

# ----- 4. symlink to ~/.claude/skills/ -----
echo "[4/6] linking skill into ~/.claude/skills/"
if [ -L "$SKILL_LINK" ]; then
  rm "$SKILL_LINK"
fi
if [ -e "$SKILL_LINK" ]; then
  echo "  ERROR: $SKILL_LINK exists and is not a symlink. Move it aside and re-run."
  exit 1
fi
ln -s "$ROOT" "$SKILL_LINK"
echo "  symlinked $SKILL_LINK -> $ROOT"

# ----- 5. user_balances.json copy -----
echo "[5/6] ensuring user_balances.json exists (gitignored)"
if [ ! -f "$ROOT/data/user_balances.json" ]; then
  cp "$ROOT/data/user_balances.example.json" "$ROOT/data/user_balances.json"
  echo "  copied example -> user_balances.json (edit to your real balances)"
else
  echo "  user_balances.json present"
fi

# ----- 6. LaunchAgent for watcher -----
echo "[6/6] installing LaunchAgent for watchlist runner"
$PYTHON_BIN "$ROOT/scripts/watch.py" --install-launchagent || \
  echo "  WARN: LaunchAgent install failed (non-fatal)"
echo ""

echo "=================================================================="
echo " DONE."
echo "=================================================================="
echo ""
echo " Next steps:"
echo "   1. Edit your Seats.aero key:    ./setup-keys.sh"
echo "   2. Edit your balances:          \$EDITOR data/user_balances.json"
echo "   3. Launch the UI:               python3 ui/server.py"
echo "                                   → open http://127.0.0.1:8721"
echo "   4. Smoke-test everything:       python3 scripts/smoke_test.py"
echo ""
echo " The skill is now usable in Claude Code — invoke flight-hacker"
echo " by mentioning flights, points, miles, or travel."
echo ""
