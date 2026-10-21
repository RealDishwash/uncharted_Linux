#!/usr/bin/env bash
# Collects PGO profile (.gcda) files gathered on the Steam Deck (or local run)
# into this worktree's pgo/ directory for BB_PGO=use builds.
#
# Usage:
#   tools/pgo/collect.sh [LABEL | REMOTE_PATH | LOCAL_DIR]
#
# Examples:
#   tools/pgo/collect.sh u1
#     -> rsync from deck@192.168.20.254:~/unch-bench/pgo/u1/ into pgo/
#   tools/pgo/collect.sh deck@192.168.20.254:~/unch-bench/pgo/latest
#     -> rsync from specified remote path into pgo/
#   tools/pgo/collect.sh /path/to/local/pgo/dump
#     -> copies local .gcda files into pgo/

set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "$0")/../.." && pwd)
TARGET_DIR="$ROOT_DIR/pgo"
mkdir -p "$TARGET_DIR"

SOURCE="${1:-latest}"
DECK_HOST="${DECK_HOST:-192.168.20.254}"
DECK_USER="${DECK_USER:-deck}"

if [[ -d "$SOURCE" ]]; then
    echo "Collecting profile files from local directory: $SOURCE"
    find "$SOURCE" -name '*.gcda' -exec cp -f {} "$TARGET_DIR/" \;
elif [[ "$SOURCE" == *:* ]]; then
    echo "Collecting profile files from remote host: $SOURCE"
    rsync -avz --include='*.gcda' --include='*/' --exclude='*' "$SOURCE/" "$TARGET_DIR/"
else
    REMOTE_PATH="~/unch-bench/pgo/$SOURCE"
    echo "Collecting profile files from Steam Deck ($DECK_USER@$DECK_HOST:$REMOTE_PATH)"
    rsync -avz --include='*.gcda' --include='*/' --exclude='*' "$DECK_USER@$DECK_HOST:$REMOTE_PATH/" "$TARGET_DIR/"
fi

COUNT=$(find "$TARGET_DIR" -name '*.gcda' | wc -l)
if [[ $COUNT -eq 0 ]]; then
    echo "Warning: no .gcda files found in $TARGET_DIR" >&2
else
    echo "Successfully collected $COUNT .gcda file(s) into $TARGET_DIR"
    du -sh "$TARGET_DIR"
fi
