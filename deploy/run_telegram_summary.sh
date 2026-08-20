#!/usr/bin/env bash
# Refresh yesterday's settlements and publish a summary only when every item
# in its immutable daily lock has a verified final outcome.

set -Eeuo pipefail
umask 077

readonly PROJECT_DIR="/root/strategy-pred"
readonly PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
readonly PORTFOLIO_SCRIPT="${PROJECT_DIR}/scripts/build_telegram_portfolio.py"
readonly PUBLISHER_SCRIPT="${PROJECT_DIR}/scripts/telegram_publisher.py"
readonly DATABASE="${PROJECT_DIR}/data/betting_journal.db"
readonly AUDIT_DB="${PROJECT_DIR}/data/telegram_publication_audit.sqlite3"
readonly CYCLE_LOCK_DIR="/run/strategy-pred"
readonly CYCLE_LOCK="${CYCLE_LOCK_DIR}/cycle.lock"
readonly TELEGRAM_LOCK_DIR="/run/strategy-pred-telegram"
readonly TELEGRAM_LOCK="${TELEGRAM_LOCK_DIR}/publication.lock"
readonly TELEGRAM_CHAT_ID="@AL7ARBET"

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export TZ="Europe/Berlin"
export TELEGRAM_CHAT_ID

current_step="bootstrap"

log() {
    local level="$1"
    shift
    printf '%s level=%s unit=telegram-summary step=%s %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$level" "$current_step" "$*"
}

on_error() {
    local rc="$1"
    local line="$2"
    log ERROR "unexpected_shell_error rc=${rc} line=${line}"
    exit "$rc"
}
trap 'on_error "$?" "$LINENO"' ERR

require_regular_file() {
    local path="$1"
    local label="$2"
    if [[ ! -f "$path" || ! -r "$path" || -L "$path" ]]; then
        log ERROR "required_file_invalid label=${label}"
        exit 66
    fi
}

for command_name in date flock mkdir; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        log ERROR "required_command_missing command=${command_name}"
        exit 69
    fi
done
if [[ ! -x "$PYTHON_BIN" ]]; then
    log ERROR "python_not_executable"
    exit 69
fi
require_regular_file "$PORTFOLIO_SCRIPT" "portfolio_builder"
require_regular_file "$PUBLISHER_SCRIPT" "telegram_publisher"
require_regular_file "$DATABASE" "betting_journal"
if [[ -e "$AUDIT_DB" && ( ! -f "$AUDIT_DB" || -L "$AUDIT_DB" ) ]]; then
    log ERROR "audit_database_invalid"
    exit 66
fi

readonly credential_directory="${CREDENTIALS_DIRECTORY:-}"
readonly credential_path="${credential_directory}/telegram_bot_token"
if [[ -z "$credential_directory" || ! -f "$credential_path" || ! -r "$credential_path" || -L "$credential_path" ]]; then
    log ERROR "telegram_credential_missing_or_invalid"
    exit 78
fi
telegram_token="$(<"$credential_path")"
if [[ ! "$telegram_token" =~ ^[0-9]{5,}:[A-Za-z0-9_-]{20,}$ ]]; then
    log ERROR "telegram_credential_format_invalid"
    exit 78
fi
export TELEGRAM_BOT_TOKEN="$telegram_token"
unset telegram_token

mkdir -p "$CYCLE_LOCK_DIR" "$TELEGRAM_LOCK_DIR"
exec 8>"$CYCLE_LOCK"
if ! flock -n 8; then
    log INFO "summary_skipped reason=strategy_cycle_active"
    exit 0
fi
exec 9>"$TELEGRAM_LOCK"
if ! flock -n 9; then
    log INFO "summary_skipped reason=telegram_publication_active"
    exit 0
fi

cd "$PROJECT_DIR"
readonly target_date="$(date --date='yesterday' '+%F')"
readonly lock_csv="${PROJECT_DIR}/reports/locked_forecasts/forecast_lock_${target_date}.csv"
readonly results_csv="${PROJECT_DIR}/reports/prediction_results_${target_date}.csv"

# No daily lock means there was no authoritative publication to summarize.
# This is an expected no-op, not permission to infer picks from current data.
current_step="locate_immutable_lock"
if [[ ! -e "$lock_csv" ]]; then
    log INFO "summary_skipped reason=no_daily_lock date=${target_date}"
    exit 0
fi
require_regular_file "$lock_csv" "daily_lock"
if [[ ! -s "$lock_csv" ]]; then
    log ERROR "daily_lock_empty_file date=${target_date}"
    exit 65
fi

current_step="refresh_results"
if results_output="$("$PYTHON_BIN" "$PORTFOLIO_SCRIPT" results \
    --date "$target_date" \
    --db "$DATABASE" \
    --lock "$lock_csv" \
    --out "$results_csv" 2>&1)"; then
    printf '%s\n' "$results_output"
else
    rc=$?
    printf '%s\n' "$results_output" >&2
    log ERROR "result_refresh_failed rc=${rc} date=${target_date}"
    exit "$rc"
fi
require_regular_file "$results_csv" "results_snapshot"

if [[ "$results_output" =~ total=([0-9]+)[[:space:]]+finished=([0-9]+)[[:space:]]+pending=([0-9]+)[[:space:]]+conflicts=([0-9]+) ]]; then
    total="${BASH_REMATCH[1]}"
    finished="${BASH_REMATCH[2]}"
    pending="${BASH_REMATCH[3]}"
    conflicts="${BASH_REMATCH[4]}"
else
    log ERROR "result_refresh_output_unparseable date=${target_date}"
    exit 65
fi

if (( total == 0 )); then
    log INFO "summary_skipped reason=no_locked_selections date=${target_date}"
    exit 0
fi
if (( conflicts > 0 )); then
    log WARN "summary_deferred reason=conflicting_results date=${target_date} conflicts=${conflicts}"
    exit 0
fi
if (( pending > 0 )); then
    log INFO "summary_deferred reason=pending_results date=${target_date} pending=${pending} total=${total}"
    exit 0
fi
if (( finished != total )); then
    log ERROR "settlement_count_invariant_failed date=${target_date} finished=${finished} total=${total}"
    exit 65
fi

# telegram_publisher performs a second fail-closed validation and filters the
# summary to the immutable selection manifest actually sent to this channel.
current_step="publish"
"$PYTHON_BIN" "$PUBLISHER_SCRIPT" summary \
    --date "$target_date" \
    --lock-csv "$lock_csv" \
    --results-csv "$results_csv" \
    --bankroll 100.00 \
    --audit-db "$AUDIT_DB" \
    --timeout 15 \
    --send

current_step="complete"
log INFO "summary_publication_completed date=${target_date}"
