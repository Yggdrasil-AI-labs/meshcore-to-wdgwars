#!/usr/bin/env bash
# Pre-release smoke test for Heimdall. Runs in CI and locally.
#
# Exercises the contained, deterministic parts of the install path:
#   1. README example linter (catches venv-form drift like the Muninn
#      v2.0.8 footgun the Pi24 user hit).
#   2. AST parse + import sanity (Heimdall is stdlib-only — no venv
#      needed for the import step).
#   3. Unit tests with the live-key safety guard explicitly opted-in
#      (matches the CI invocation, surfaces guard regressions).
#   4. heimdall.py --version + --help sanity.
#   5. --schedule headless renders the systemd unit with --dry-run +
#      marker, in an XDG-isolated home. Linux+systemd only — macOS
#      gets cron and Windows gets schtasks (both bump into a 261-char
#      cap once temp-dir paths get long). CI runs Linux, so we focus
#      there. Aligned with wigle's gate.
#
# Live `--schedule` install against the real systemd user manager is
# NOT part of this script — it requires a clean host and a side-effecting
# systemctl --user environment. That belongs in a pre-release manual
# checklist with a sacrificial key.
#
# Run from the repo root:   bash scripts/smoke.sh
# Exit: 0 all pass, 1 any failure (fail-fast).

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d -t heimdall-smoke-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

say()  { printf "[smoke] %s\n" "$*"; }
fail() { printf "[smoke] FAIL: %s\n" "$*" >&2; exit 1; }
ok()   { printf "[smoke] ok: %s\n" "$*"; }

cd "$REPO_DIR"

# ─── 1. README example linter ───
say "linting README examples..."
if python3 scripts/check_readme_examples.py README.md > "$TMP_DIR/lint.log" 2>&1; then
    ok "README clean"
else
    cat "$TMP_DIR/lint.log" >&2
    fail "README linter"
fi

# ─── 2. AST parse + import sanity ───
say "AST parse + module import..."
python3 -c "import ast; ast.parse(open('heimdall.py', encoding='utf-8').read())" \
    || fail "heimdall.py ast parse"
python3 -c "import heimdall; print('version', heimdall.__version__)" \
    || fail "module import"
ok "parse + import"

# ─── 3. unit tests with the live-key safety guard explicitly opted-in ───
say "running unit tests..."
if HEIMDALL_TEST_ALLOW_LIVE_KEY=1 python3 -m unittest discover tests/ \
        > "$TMP_DIR/tests.log" 2>&1; then
    ok "tests passed"
else
    tail -30 "$TMP_DIR/tests.log" >&2
    fail "unit tests"
fi

# ─── 4. CLI sanity ───
say "heimdall.py --version..."
VER=$(python3 heimdall.py --version 2>&1 | head -1) \
    || fail "--version"
say "  $VER"
python3 heimdall.py --help > /dev/null || fail "--help"
ok "--version + --help"

# ─── 5. --schedule headless: write unit file to a temp XDG and assert ───
if [ "$(uname -s)" = "Linux" ] && command -v systemctl >/dev/null 2>&1 \
        && [ -d /run/systemd/system ]; then
    say "rendering systemd unit (no install) — XDG-isolated..."
    export HOME="$TMP_DIR/home"
    mkdir -p "$HOME/.config/heimdall"
    echo "PLACEHOLDER" > "$HOME/.config/heimdall/api.key"
    export XDG_CONFIG_HOME="$HOME/.config"
    SCHED_CSV="$TMP_DIR/nightly.csv"
    touch "$SCHED_CSV"
    # Suppress systemctl errors — unit file is written BEFORE the call.
    python3 heimdall.py --schedule \
        --schedule-csv "$SCHED_CSV" \
        --schedule-time 03:00 \
        --schedule-dry-run > "$TMP_DIR/sched.log" 2>&1 || true
    UNIT="$XDG_CONFIG_HOME/systemd/user/heimdall.service"
    TIMER="$XDG_CONFIG_HOME/systemd/user/heimdall.timer"
    if [ ! -f "$UNIT" ]; then
        cat "$TMP_DIR/sched.log" >&2
        fail "no service file written to $UNIT"
    fi
    if [ ! -f "$TIMER" ]; then
        cat "$TMP_DIR/sched.log" >&2
        fail "no timer file written to $TIMER"
    fi
    grep -q "Description=Heimdall daily MeshCore push \[DRY-RUN\]" "$UNIT" \
        || fail "dry-run marker missing from service Description"
    grep -q -- "--dry-run" "$UNIT" \
        || fail "ExecStart missing --dry-run"
    grep -q "# managed-by-heimdall" "$UNIT" \
        || fail "marker comment missing from service"
    grep -q "OnCalendar=\*-\*-\* 03:00:00" "$TIMER" \
        || fail "OnCalendar missing from timer"
    # CRITICAL: ensure no API key ever lands in the unit file
    grep -q -- "--key " "$UNIT" \
        && fail "WDGoWars key leaked into service ExecStart"
    grep -q "PLACEHOLDER" "$UNIT" \
        && fail "saved key contents leaked into service ExecStart"
    ok "unit + timer content correct (dry-run + marker + flags + no key leak)"
else
    say "(skipping systemd unit smoke — not on a systemd Linux host)"
fi

say "all smoke checks passed"
exit 0
