#!/bin/bash

set -eu
[[ $DEBUG = true ]] && set -x
_here=`dirname $(realpath $0)`
INDEX_DIR="CRATOS_IO_INDEX_DIR/index"
CRATES_DIR="TUNASYNC_WORKING_DIR/crates"
STATE_DIR="TUNASYNC_WORKING_DIR/state"
export GITSYNC_REFLOG_EXPIRE=7.days

mkdir -p "$INDEX_DIR" "$CRATES_DIR" "$STATE_DIR"

# Reuse the existing crates.io-index image logic, but keep the index checkout
# separate from crate tarballs to avoid unnecessary git and filesystem churn.
# If any of these steps fail, the sync script shall be stopped.
TO="$INDEX_DIR" /sync-crates-index.sh && \
python3 ${_here}/sync-crates.py \
    --index "$INDEX_DIR" \
    --crates "$CRATES_DIR" \
    --state "$STATE_DIR" && \
python3 ${_here}/cleanup-crates.py \
    --index "$INDEX_DIR" \
    --crates "$CRATES_DIR"
