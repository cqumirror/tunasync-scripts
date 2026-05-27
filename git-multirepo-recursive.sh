#!/bin/bash

CONFIG_FILE="${1:-repos.json}"
SYNC_SCRIPT=${TUNASYNC_GIT_RECURSIVE_SCRIPT_PATH:-"./git-recursive.sh"}
MIRROR_BASE_URL=${MIRROR_BASE_URL:-"https://mirrors.cqu.edu.cn/git/"}
WORKING_DIR_BASE=${TUNASYNC_WORKING_DIR:-"/data/mirrors/git/"}

jq -c '.repositories[]' "$CONFIG_FILE" | while read -r repo; do
    name=$(echo "$repo" | jq -r '.name')
    upstream=$(echo "$repo" | jq -r '.upstream')
    rel_path=$(echo "$repo" | jq -r '.generated_script')
    
    export TUNASYNC_WORKING_DIR="${WORKING_DIR_BASE}/${name}.git"
    export GENERATED_SCRIPT="${WORKING_DIR_BASE}/${rel_path}"
    
    echo "Sync Repo: $name"
    echo "Working Dir: $TUNASYNC_WORKING_DIR"
    echo "Generated Script: $GENERATED_SCRIPT"

    export TUNASYNC_UPSTREAM_URL="$upstream"
    export RECURSIVE=1
    export MIRROR_BASE_URL="$MIRROR_BASE_URL"
    mkdir -p "$(dirname "$GENERATED_SCRIPT")"

    if ! "$SYNC_SCRIPT"; then
        echo "Failed to sync" 
    fi
done
total_size=$(du -sb "${WORKING_DIR_BASE}"|cut -f1)
echo "Total size is" $(numfmt --to=iec $total_size)