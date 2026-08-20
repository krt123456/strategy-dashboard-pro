#!/usr/bin/env bash
# Publish only the already-built static dashboard to a small dedicated branch.
# This script never reads, copies, stages, or pushes the SQLite database.

set -Eeuo pipefail
umask 077

readonly PROJECT_DIR="${PROJECT_DIR:-/root/strategy-pred}"
readonly DASHBOARD_DIR="${PROJECT_DIR}/dashboard"
readonly REMOTE_NAME="${REMOTE_NAME:-origin}"
readonly PUBLISH_BRANCH="${PUBLISH_BRANCH:-pages-prebuilt}"
readonly LOCK_DIR="${PUBLISH_LOCK_DIR:-/run/strategy-pred-pages}"
readonly LOCK_FILE="${LOCK_DIR}/publish.lock"
readonly MAX_FILE_BYTES=10485760
readonly MAX_PAYLOAD_BYTES=26214400

temp_dir=""

log() {
    local level="$1"
    shift
    printf '%s level=%s component=pages-publisher %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$level" "$*"
}

cleanup() {
    if [[ -z "$temp_dir" ]]; then
        return
    fi
    case "$temp_dir" in
        /tmp/strategy-pages.*)
            rm -rf --one-file-system -- "$temp_dir"
            ;;
        *)
            log ERROR "temporary_directory_guard_failed"
            ;;
    esac
}
trap cleanup EXIT

fail() {
    log ERROR "$*"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required_command_missing command=$1"
}

require_command git
require_command ssh
require_command sha256sum
require_command stat
require_command realpath
require_command flock

if [[ ! "$PUBLISH_BRANCH" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] \
    || [[ "$PUBLISH_BRANCH" == *".."* ]] \
    || [[ "$PUBLISH_BRANCH" == *"@{"* ]]; then
    fail "invalid_publish_branch"
fi
[[ "$REMOTE_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || fail "invalid_remote_name"

[[ -d "$PROJECT_DIR/.git" ]] || fail "project_git_directory_missing"
[[ -d "$DASHBOARD_DIR" ]] || fail "dashboard_directory_missing"

readonly expected_assets=(index.html manifest.json sw.js icon.png)
payload_bytes=0
for asset in "${expected_assets[@]}"; do
    source_path="${DASHBOARD_DIR}/${asset}"
    [[ -f "$source_path" && ! -L "$source_path" ]] \
        || fail "dashboard_asset_missing_or_unsafe asset=${asset}"
    asset_bytes="$(stat -c '%s' "$source_path")"
    (( asset_bytes > 0 )) || fail "dashboard_asset_empty asset=${asset}"
    (( asset_bytes <= MAX_FILE_BYTES )) \
        || fail "dashboard_asset_too_large asset=${asset}"
    payload_bytes=$((payload_bytes + asset_bytes))
done
(( payload_bytes <= MAX_PAYLOAD_BYTES )) || fail "dashboard_payload_too_large"

# Accept only credential-free GitHub remote syntax. Authentication remains in
# the host's SSH agent/key configuration and is never copied into the temp repo.
remote_url="$(git -C "$PROJECT_DIR" config --get "remote.${REMOTE_NAME}.url" 2>/dev/null || true)"
if [[ ! "$remote_url" =~ ^git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] \
    && [[ ! "$remote_url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]]; then
    fail "unsupported_or_credential_bearing_remote"
fi

mkdir -p "$LOCK_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log INFO "publish_skipped reason=lock_held"
    exit 0
fi

temp_dir="$(mktemp -d -p /tmp strategy-pages.XXXXXXXX)"
temp_dir="$(realpath "$temp_dir")"
[[ "$temp_dir" == /tmp/strategy-pages.* ]] \
    || fail "temporary_directory_guard_failed"
readonly publish_repo="${temp_dir}/repo"
mkdir -p "$publish_repo"

git -C "$publish_repo" init --quiet
git -C "$publish_repo" remote add origin "$remote_url"
git -C "$publish_repo" config user.name "Strategy Dashboard Publisher"
git -C "$publish_repo" config user.email "strategy-dashboard-publisher@users.noreply.github.com"
git -C "$publish_repo" config core.autocrlf false
git -C "$publish_repo" config core.eol lf

export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes"

remote_branch=""
if ! remote_branch="$(git -C "$publish_repo" ls-remote --heads origin \
    "refs/heads/${PUBLISH_BRANCH}" 2>/dev/null)"; then
    fail "remote_branch_lookup_failed"
fi

if [[ -n "$remote_branch" ]]; then
    if ! git -C "$publish_repo" fetch --quiet --depth=1 origin \
        "refs/heads/${PUBLISH_BRANCH}" >/dev/null 2>&1; then
        fail "remote_branch_fetch_failed"
    fi
    git -C "$publish_repo" checkout --quiet --detach FETCH_HEAD

    while IFS= read -r tracked_path; do
        case "$tracked_path" in
            .gitattributes|dashboard/.nojekyll|dashboard/SHA256SUMS|dashboard/icon.png|dashboard/index.html|dashboard/manifest.json|dashboard/sw.js)
                ;;
            *)
                fail "unexpected_tracked_path_on_publish_branch"
                ;;
        esac
    done < <(git -C "$publish_repo" ls-tree -r --name-only HEAD)

    git -C "$publish_repo" rm --quiet --ignore-unmatch -- \
        .gitattributes \
        dashboard/.nojekyll \
        dashboard/SHA256SUMS \
        dashboard/icon.png \
        dashboard/index.html \
        dashboard/manifest.json \
        dashboard/sw.js
else
    git -C "$publish_repo" checkout --quiet --orphan "$PUBLISH_BRANCH"
fi

mkdir -p "$publish_repo/dashboard"
printf '%s\n' \
    '/dashboard/.nojekyll -text' \
    '/dashboard/SHA256SUMS -text' \
    '/dashboard/icon.png -text' \
    '/dashboard/index.html -text' \
    '/dashboard/manifest.json -text' \
    '/dashboard/sw.js -text' \
    > "$publish_repo/.gitattributes"
for asset in "${expected_assets[@]}"; do
    install -m 0644 "${DASHBOARD_DIR}/${asset}" \
        "${publish_repo}/dashboard/${asset}"
done
install -m 0644 /dev/null "$publish_repo/dashboard/.nojekyll"
(
    cd "$publish_repo/dashboard"
    sha256sum icon.png index.html manifest.json sw.js > SHA256SUMS
)

git -C "$publish_repo" add -- \
    .gitattributes \
    dashboard/.nojekyll \
    dashboard/SHA256SUMS \
    dashboard/icon.png \
    dashboard/index.html \
    dashboard/manifest.json \
    dashboard/sw.js

if git -C "$publish_repo" diff --cached --quiet; then
    log INFO "publish_skipped reason=no_dashboard_change branch=${PUBLISH_BRANCH}"
    exit 0
fi

dashboard_digest="$(sha256sum "$publish_repo/dashboard/index.html" | cut -c1-12)"
git -C "$publish_repo" commit --quiet \
    -m "publish: prebuilt dashboard ${dashboard_digest}"

# A normal fast-forward push is deliberate. Concurrent or divergent updates
# fail closed and are retried on the next timer run; this script never forces.
if ! git -C "$publish_repo" push --quiet origin \
    "HEAD:refs/heads/${PUBLISH_BRANCH}" >/dev/null 2>&1; then
    fail "publish_push_rejected_or_failed"
fi

log INFO "dashboard_published branch=${PUBLISH_BRANCH} digest=${dashboard_digest}"
