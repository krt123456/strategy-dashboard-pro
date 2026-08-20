#!/usr/bin/env bash
# Build one immutable, approved daily portfolio and publish it through the
# Telegram Bot API. The bot token is supplied only by systemd LoadCredential.

set -Eeuo pipefail
umask 077

readonly PROJECT_DIR="/root/strategy-pred"
readonly PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
readonly PORTFOLIO_SCRIPT="${PROJECT_DIR}/scripts/build_telegram_portfolio.py"
readonly PUBLISHER_SCRIPT="${PROJECT_DIR}/scripts/telegram_publisher.py"
readonly DATABASE="${PROJECT_DIR}/data/betting_journal.db"
readonly LINEFEED="${PROJECT_DIR}/data/one_xbet_linefeed_snapshot.csv"
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
    printf '%s level=%s unit=telegram-daily step=%s %s\n' \
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
require_regular_file "$LINEFEED" "linefeed_snapshot"
if [[ -e "$AUDIT_DB" && ( ! -f "$AUDIT_DB" || -L "$AUDIT_DB" ) ]]; then
    log ERROR "audit_database_invalid"
    exit 66
fi

# systemd materializes LoadCredential entries below CREDENTIALS_DIRECTORY.
# Read it without printing it, reject malformed values, then discard the shell
# variable after copying it to the child-process environment.
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

# Do not read the rollback-journal SQLite database while the trusted cycle is
# resolving results. Later timer attempts provide automatic recovery.
exec 8>"$CYCLE_LOCK"
if ! flock -w 60 8; then
    log ERROR "publication_aborted reason=strategy_cycle_lock_timeout"
    exit 75
fi
exec 9>"$TELEGRAM_LOCK"
if ! flock -n 9; then
    log INFO "publication_skipped reason=telegram_publication_active"
    exit 0
fi

cd "$PROJECT_DIR"
readonly target_date="$(date '+%F')"
readonly lock_csv="${PROJECT_DIR}/reports/locked_forecasts/forecast_lock_${target_date}.csv"

current_step="build_immutable_lock"
if [[ -e "$lock_csv" ]]; then
    if [[ ! -f "$lock_csv" || ! -r "$lock_csv" || ! -s "$lock_csv" || -L "$lock_csv" ]]; then
        log ERROR "existing_daily_lock_invalid date=${target_date}"
        exit 66
    fi
    log INFO "immutable_lock_reused date=${target_date}"
else
    "$PYTHON_BIN" "$PORTFOLIO_SCRIPT" daily \
        --date "$target_date" \
        --db "$DATABASE" \
        --linefeed "$LINEFEED" \
        --out "$lock_csv" \
        --min-lead-minutes 60 \
        --max-picks 5
    require_regular_file "$lock_csv" "daily_lock"
    if [[ ! -s "$lock_csv" ]]; then
        log ERROR "daily_lock_empty_file date=${target_date}"
        exit 65
    fi
fi

current_step="publish"
"$PYTHON_BIN" "$PUBLISHER_SCRIPT" daily \
    --date "$target_date" \
    --lock-csv "$lock_csv" \
    --bankroll 100.00 \
    --min-lead-minutes 60 \
    --audit-db "$AUDIT_DB" \
    --timeout 15 \
    --send

current_step="complete"
log INFO "daily_publication_completed date=${target_date}"
