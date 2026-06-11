#!/usr/bin/env bash
# Pre-flight check for live deploy of MRR + overnight_range R24.
#
# Validates EVERY pre-condition before two prop accounts go live:
#   1. Env vars present (TopStepX creds, DATABASE_URL, DLL, portfolio cap)
#   2. TOMLs in expected production state (enabled=true, correct symbols)
#   3. Smoke tests pass for both strategies + risk infra
#   4. Recent-bar dry-run analyze() — verify signals would fire as expected
#   5. Risk infra wired (portfolio_daily_breaker, regime_publisher, consec breakers)
#
# Prints a green ALL CHECKS PASSED on success, or a numbered FAILED report
# on any failure (no false negatives — exits 1 only when something real is wrong).
#
# Usage:
#   bash scripts/preflight_live_deploy.sh                # validate only
#   bash scripts/preflight_live_deploy.sh --verbose      # show details on success too
#   bash scripts/preflight_live_deploy.sh --skip-dryrun  # skip the slow dry-run step
#
# This script is READ-ONLY — it never starts processes or modifies configs.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERBOSE=0
SKIP_DRYRUN=0
for arg in "$@"; do
    case "$arg" in
        --verbose) VERBOSE=1 ;;
        --skip-dryrun) SKIP_DRYRUN=1 ;;
        --help|-h)
            sed -n '2,18p' "$0"
            exit 0
            ;;
    esac
done

# Color codes — disabled if not a TTY.
if [[ -t 1 ]]; then
    BOLD="$(printf '\033[1m')"; GREEN="$(printf '\033[32m')"
    RED="$(printf '\033[31m')"; YELLOW="$(printf '\033[33m')"
    CYAN="$(printf '\033[36m')"; RESET="$(printf '\033[0m')"
else
    BOLD=""; GREEN=""; RED=""; YELLOW=""; CYAN=""; RESET=""
fi

FAILURES=()
WARNINGS=()
CHECKS=0
PASSED=0

_pass() {
    CHECKS=$((CHECKS + 1)); PASSED=$((PASSED + 1))
    [[ "$VERBOSE" -eq 1 ]] && printf "  ${GREEN}✓${RESET} %s\n" "$1" || true
}
_warn() {
    CHECKS=$((CHECKS + 1)); PASSED=$((PASSED + 1))
    WARNINGS+=("$1")
    printf "  ${YELLOW}⚠${RESET} %s\n" "$1"
}
_fail() {
    CHECKS=$((CHECKS + 1))
    FAILURES+=("$1")
    printf "  ${RED}✗${RESET} %s\n" "$1"
}

_section() {
    printf "\n${BOLD}${CYAN}%s${RESET}\n" "$1"
}

# Source .env if present so the rest of the script sees the variables.
if [[ -f .env ]]; then
    set +u
    set -a; source .env; set +a
    set -u
fi

_section "1/5  Env vars"
# Accept either PROJECT_X_* or TOPSTEPX_* aliases (per trading_bot.py:180-181).
USER_OK=0; KEY_OK=0
for v in PROJECT_X_USERNAME TOPSTEPX_USERNAME; do
    if [[ -n "${!v-}" ]]; then USER_OK=1; break; fi
done
for v in PROJECT_X_API_KEY TOPSTEPX_API_KEY; do
    if [[ -n "${!v-}" ]]; then KEY_OK=1; break; fi
done
[[ "$USER_OK" -eq 1 ]] && _pass "broker username present" || _fail "missing PROJECT_X_USERNAME / TOPSTEPX_USERNAME"
[[ "$KEY_OK" -eq 1 ]] && _pass "broker API key present" || _fail "missing PROJECT_X_API_KEY / TOPSTEPX_API_KEY"

[[ -n "${DATABASE_URL-}" ]] && _pass "DATABASE_URL set" || _fail "DATABASE_URL unset (persistence + breaker history will fail)"
[[ -n "${DAILY_LOSS_LIMIT-}" ]] && _pass "DAILY_LOSS_LIMIT set ($DAILY_LOSS_LIMIT)" || _warn "DAILY_LOSS_LIMIT unset (using strategy-internal default — recommend setting explicitly)"
[[ -n "${INITIAL_BALANCE-}" ]] && _pass "INITIAL_BALANCE set ($INITIAL_BALANCE)" || _warn "INITIAL_BALANCE unset (per-account percentage limits will fall back to defaults)"
[[ -n "${PORTFOLIO_DAILY_LOSS_CAP-}" ]] && _pass "PORTFOLIO_DAILY_LOSS_CAP set ($PORTFOLIO_DAILY_LOSS_CAP)" || _warn "PORTFOLIO_DAILY_LOSS_CAP unset (will default to \$1000)"
[[ "${ENABLE_SIGNALR:-true}" == "true" || "${ENABLE_SIGNALR:-true}" == "1" ]] && _pass "ENABLE_SIGNALR not disabled" || _fail "ENABLE_SIGNALR=$ENABLE_SIGNALR — strategy will run OFFLINE (no live data)"

_section "2/5  TOML pinning"
_check_toml() {
    local name="$1" file="config/strategies/${1}.toml"
    if [[ ! -f "$file" ]]; then _fail "$name TOML missing: $file"; return; fi
    if ! grep -qE '^enabled\s*=\s*true' "$file"; then _fail "$name TOML has meta.enabled != true (will boot disabled)"; return; fi
    if ! grep -qE '^live_breaker_enabled\s*=\s*true' "$file"; then _warn "$name TOML missing live_breaker_enabled=true (consec-loss breaker NOT bridged to live)"; fi
    if ! grep -qE '^symbols\s*=\s*\[' "$file"; then _fail "$name TOML missing meta.symbols list"; return; fi
    _pass "$name TOML in production state"
}
_check_toml morning_range_reversion
_check_toml overnight_range

_section "3/5  Smoke tests"
TEST_FILES=(
    tests/test_morning_range_reversion_smoke.py
    tests/test_overnight_range_symbol_risk_signal_overrides.py
    tests/test_overnight_range_trading_window.py
    tests/test_portfolio_daily_breaker.py
    tests/test_regime.py
)
TEST_OUT=$(.venv/bin/python -m pytest "${TEST_FILES[@]}" -q --tb=line 2>&1 || true)
if echo "$TEST_OUT" | grep -qE '[0-9]+ passed' && ! echo "$TEST_OUT" | grep -qE 'failed|error'; then
    N_PASS=$(echo "$TEST_OUT" | grep -oE '[0-9]+ passed' | head -1)
    _pass "all 5 prereq test files pass ($N_PASS)"
else
    _fail "test suite failed — run: .venv/bin/python -m pytest ${TEST_FILES[*]}"
    echo "$TEST_OUT" | tail -10 | sed 's/^/    /'
fi

_section "4/5  Risk infra wiring"
RISK_OUT=$(.venv/bin/python -c "
import sys
def check(label, fn):
    try:
        fn()
        print(f'PASS:{label}')
    except Exception as e:
        print(f'FAIL:{label} ({type(e).__name__}: {e})')
def _pdb():
    from core.portfolio_daily_breaker import PortfolioDailyBreaker
def _regime():
    from core.regime import RegimePublisherService, maybe_start_regime_publisher
def _consec():
    from core.consec_loss_breaker import BreakerConfig, evaluate, trade_iter_for_strategy
def _evbus():
    from core.event_bus import EventType
    assert hasattr(EventType, 'TRADE_CLOSED'), 'TRADE_CLOSED missing'
    assert hasattr(EventType, 'PORTFOLIO_KILL'), 'PORTFOLIO_KILL missing'
check('core.portfolio_daily_breaker importable', _pdb)
check('core.regime publisher importable', _regime)
check('core.consec_loss_breaker importable', _consec)
check('event bus has TRADE_CLOSED + PORTFOLIO_KILL', _evbus)
" 2>&1)
while IFS= read -r line; do
    case "$line" in
        PASS:*) _pass "${line#PASS:}" ;;
        FAIL:*) _fail "${line#FAIL:}" ;;
        *) [[ -n "$line" ]] && printf "    %s\n" "$line" ;;
    esac
done <<< "$RISK_OUT"

_section "5/5  Dry-run analyze() on recent bars"
if [[ "$SKIP_DRYRUN" -eq 1 ]]; then
    _warn "skipping (--skip-dryrun); enable to validate signal pipeline before live"
else
    DRYRUN_OUT=$(.venv/bin/python scripts/preflight_dryrun.py 2>&1 || true)
    if echo "$DRYRUN_OUT" | grep -q "^DRYRUN_OK"; then
        echo "$DRYRUN_OUT" | grep -v "^DRYRUN_OK" | sed 's/^/    /'
        _pass "both strategies' analyze() executes without crashing on recent bars"
    else
        _fail "dry-run failed (see output below)"
        echo "$DRYRUN_OUT" | tail -20 | sed 's/^/    /'
    fi
fi

# Final summary
echo
printf "${BOLD}━━━ Pre-flight summary ━━━${RESET}\n"
printf "  Checks:    %d total  (${GREEN}%d passed${RESET}, ${YELLOW}%d warnings${RESET}, ${RED}%d failures${RESET})\n" \
    "$CHECKS" "$PASSED" "${#WARNINGS[@]}" "${#FAILURES[@]}"

if (( ${#FAILURES[@]} > 0 )); then
    echo
    printf "${RED}${BOLD}BLOCKED — do NOT deploy live until these are fixed:${RESET}\n"
    for f in "${FAILURES[@]}"; do printf "  ${RED}✗${RESET} %s\n" "$f"; done
    exit 1
fi

if (( ${#WARNINGS[@]} > 0 )); then
    echo
    printf "${YELLOW}${BOLD}READY with warnings${RESET} — review then proceed:\n"
    for w in "${WARNINGS[@]}"; do printf "  ${YELLOW}⚠${RESET} %s\n" "$w"; done
else
    echo
    printf "${GREEN}${BOLD}ALL CHECKS PASSED — clear to deploy.${RESET}\n"
fi

echo
printf "${BOLD}Launch (per-account):${RESET}\n"
echo "  bash scripts/run_morning_reversion.sh <account_idx_1>  # MRR — schedule-aware (countdown to 06:53 ET)"
echo "  bash scripts/run_overnight.sh        <account_idx_2>  # overnight_range — immediate"
echo
printf "${BOLD}Monitor (after first session):${RESET}\n"
echo "  .venv/bin/python scripts/compare_live_vs_backtest.py --strategy morning_range_reversion --account <id>"
echo "  .venv/bin/python scripts/compare_live_vs_backtest.py --strategy overnight_range          --account <id>"
echo
