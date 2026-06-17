#!/usr/bin/env bash
# uninstall_mrr_launchd.sh - Unload + remove the daily MRR launch agent.
#
# Counterpart to scripts/install_mrr_launchd.sh.  Stops the launchd agent
# (if loaded) and deletes the plist from ~/Library/LaunchAgents/.
#
# Usage:
#   bash scripts/uninstall_mrr_launchd.sh 1            # remove account 1's agent
#   bash scripts/uninstall_mrr_launchd.sh 1 --keep     # unload but keep the plist file

set -euo pipefail

ACCOUNT_NUM="${1:-}"
KEEP_PLIST=0
shift || true
while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep) KEEP_PLIST=1; shift ;;
        --help|-h)
            echo "Usage: $0 <account_num> [--keep]"
            echo "  --keep   Unload from launchd but leave the plist file on disk."
            exit 0
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$ACCOUNT_NUM" ]; then
    echo "Error: account number required."
    echo "Usage: $0 <account_num> [--keep]"
    exit 1
fi

LABEL="com.tradebot.mrr.account${ACCOUNT_NUM}"
TARGET_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"

# Unload (bootout = modern path; unload -w = legacy fallback).
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    echo "  → unloading ${LABEL}..."
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || \
        launchctl unload -w "$TARGET_PATH" 2>/dev/null || \
        echo "  ⚠️  launchctl unload returned non-zero; agent may have been in a half-state"
    echo "  ✓ unloaded"
else
    echo "  ℹ️  ${LABEL} not currently loaded by launchctl (already stopped or never installed)."
fi

if [ "$KEEP_PLIST" -eq 1 ]; then
    if [ -f "$TARGET_PATH" ]; then
        echo "  --keep: leaving plist at ${TARGET_PATH/#${HOME}/~}"
    fi
    exit 0
fi

if [ -f "$TARGET_PATH" ]; then
    rm -f "$TARGET_PATH"
    echo "  ✓ removed ${TARGET_PATH/#${HOME}/~}"
else
    echo "  ℹ️  plist not on disk at ${TARGET_PATH/#${HOME}/~} (nothing to remove)."
fi

cat <<EOF

  ${LABEL} is uninstalled.  To re-install:
    bash scripts/install_mrr_launchd.sh ${ACCOUNT_NUM}
EOF
