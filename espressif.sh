#!/bin/bash

CONFIG_FILE="${1:-repos.json}"
SYNC_SCRIPT=${TUNASYNC_GIT_RECURSIVE_SCRIPT_PATH:-"./git-recursive.sh"}

exit_code=0

jq -c '.repositories[]' "$CONFIG_FILE" | while read -r repo; do
    name=$(echo "$repo" | jq -r '.name')
    upstream=$(echo "$repo" | jq -r '.upstream')
    generated_script=$TUNASYNC_WORKING_DIR/$(echo "$repo" | jq -r '.generated_script')
    
    echo "Sync Repo: $name"
    echo "Generated Script: $generated_script"
    
    # 设置环境变量
    export TUNASYNC_UPSTREAM_URL="$upstream"
    export GENERATED_SCRIPT="$generated_script"
    export RECURSIVE=1
    
    # 调用同步脚本
    if ! "$SYNC_SCRIPT"; then
        exit_code=$?
    fi
done