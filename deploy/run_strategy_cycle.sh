#!/usr/bin/env bash
# Trusted Strategy-Pred cycle. This script intentionally contains no Telegram
# publishing and no git operations.

set -Eeuo pipefail
umask 077

readonly PROJECT_DIR="/root/strategy-pred"
readonly LOCK_DIR="/run/strategy-pred"
readonly LOCK_FILE="${LOCK_DIR}/cycle.lock"
readonly PYTHON_BIN="${PYTHON_BIN:-/root/strategy-pred/.venv/bin/python}"

# Keep command resolution deterministic under systemd and manual recovery runs.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export TZ="UTC"

current_step="bootstrap"
critical_failures=0
resolver_failures=0

log() {
    local level="$1"
    shift
    printf '%s level=%s run=%s step=%s %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "$level" \
        "${run_id:-not-started}" \
        "$current_step" \
        "$*"
}

on_unexpected_error() {
    local rc="$1"
    local line="$2"
    log ERROR "unexpected_shell_error rc=${rc} line=${line}"
    exit "$rc"
}
trap 'on_unexpected_error "$?" "$LINENO"' ERR

require_command() {
    local command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        log ERROR "required_command_missing command=${command_name}"
        exit 69
    fi
}

# Run a command with a hard deadline while preserving its actual exit status.
# Output from the command goes to journald through the service configuration.
run_timed_step() {
    local step_name="$1"
    local deadline="$2"
    shift 2

    current_step="$step_name"
    local started_at
    local finished_at
    local elapsed
    local rc
    started_at="$(date -u +%s)"
    log INFO "started timeout=${deadline}"

    if timeout --signal=TERM --kill-after=20s "$deadline" "$@"; then
        rc=0
    else
        rc=$?
    fi

    finished_at="$(date -u +%s)"
    elapsed=$((finished_at - started_at))
    if [[ "$rc" -eq 0 ]]; then
        log INFO "finished rc=0 elapsed_seconds=${elapsed}"
    elif [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
        log WARN "timed_out rc=${rc} elapsed_seconds=${elapsed}"
    else
        log WARN "failed rc=${rc} elapsed_seconds=${elapsed}"
    fi
    return "$rc"
}

run_resolver() {
    local step_name="$1"
    local deadline="$2"
    shift 2
    if ! run_timed_step "$step_name" "$deadline" "$@"; then
        resolver_failures=$((resolver_failures + 1))
        log WARN "resolver_failure_ignored continuation=enabled"
    fi
}

require_command flock
require_command timeout
require_command date
if [[ ! -x "$PYTHON_BIN" ]]; then
    log ERROR "python_not_executable path=${PYTHON_BIN}"
    exit 69
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
    log ERROR "project_directory_missing path=${PROJECT_DIR}"
    exit 66
fi
if [[ ! -r "${PROJECT_DIR}/.env" ]]; then
    # systemd loads this file. Never source, echo, or inspect it here.
    log ERROR "environment_file_missing_or_unreadable"
    exit 78
fi

mkdir -p "$LOCK_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log INFO "cycle_skipped reason=lock_held"
    exit 0
fi

cd "$PROJECT_DIR"
readonly run_id="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
readonly today_utc="$(date -u '+%F')"
readonly yesterday_utc="$(date -u -d 'yesterday' '+%F')"

current_step="cycle"
log INFO "cycle_started target_date=${today_utc} resolution_anchor=${yesterday_utc}"

# Fail closed for prediction generation. A failed snapshot must never cause the
# registry/runner to reuse stale fixtures as if they were current.
snapshot_ok=0
if run_timed_step \
    "snapshot_today" \
    "5m" \
    "$PYTHON_BIN" scripts/one_xbet_linefeed_snapshot.py \
        --date "$today_utc" \
        --count 500 \
        --timeout 15; then
    snapshot_ok=1
else
    critical_failures=$((critical_failures + 1))
fi

registry_ok=0
if [[ "$snapshot_ok" -eq 1 ]]; then
    if run_timed_step \
        "data_source_registry" \
        "3m" \
        "$PYTHON_BIN" scripts/data_source_registry.py; then
        registry_ok=1
    else
        critical_failures=$((critical_failures + 1))
    fi
else
    current_step="data_source_registry"
    log WARN "skipped reason=snapshot_failed stale_input_guard=enabled"
fi

if [[ "$registry_ok" -eq 1 ]]; then
    if ! run_timed_step \
        "cross_source_strategy_runner" \
        "8m" \
        "$PYTHON_BIN" scripts/cross_source_strategy_runner.py \
            --date "$today_utc" \
            --min-lead-minutes 15; then
        critical_failures=$((critical_failures + 1))
    fi
else
    current_step="cross_source_strategy_runner"
    log WARN "skipped reason=registry_not_fresh stale_input_guard=enabled"
fi

# Resolver failures are isolated. One blocked site or exhausted API key must not
# prevent the other resolvers or the dashboard rebuild from running.
run_resolver \
    "resolve_betexplorer" \
    "7m" \
    "$PYTHON_BIN" scripts/resolve_results_betexplorer.py \
        --date "$yesterday_utc" \
        --days-back 3 \
        --min-match-score 4 \
        --quiet

run_resolver \
    "resolve_api_sports" \
    "5m" \
    "$PYTHON_BIN" scripts/resolve_results_api_sports.py \
        --date "$yesterday_utc" \
        --days-back 3

run_resolver \
    "resolve_flashscore" \
    "7m" \
    "$PYTHON_BIN" scripts/flashscore_resolver.py \
        --date "$yesterday_utc" \
        --quiet

# Always rebuild the local dashboard from whatever verified state is available.
if ! run_timed_step \
    "build_dashboard" \
    "3m" \
    "$PYTHON_BIN" scripts/build_dashboard.py; then
    critical_failures=$((critical_failures + 1))
fi

current_step="cycle"
if [[ "$critical_failures" -gt 0 ]]; then
    log ERROR "cycle_failed critical_failures=${critical_failures} resolver_failures=${resolver_failures}"
    exit 1
fi

if [[ "$resolver_failures" -gt 0 ]]; then
    log WARN "cycle_completed_with_resolver_warnings resolver_failures=${resolver_failures}"
else
    log INFO "cycle_completed resolver_failures=0"
fi
exit 0
