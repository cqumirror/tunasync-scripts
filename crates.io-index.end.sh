#!/bin/bash
cat > $TUNASYNC_WORKING_DIR/config.json << 'EOF'
{
  "dl": "https://mirrors.cqu.edu.cn/crates.io/api/v1/crates",
  "api": "https://crates.io",
  "canonical": "https://mirrors.cqu.edu.cn/crates.io-index/"
}
EOF