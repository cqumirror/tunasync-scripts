#!/bin/bash

EOF
CRATES_PROXY="${CRATES_PROXY:-https://mirrors.cqu.edu.cn/crates.io/api/v1/crates}"
CRATES_GITMSG="${CRATES_GITMSG:-Redirect to CQU Mirrors}"
CRATES_GITMAIL="${CRATES_GITMAIL:-cqumirror@gmail.com}"
CRATES_GITNAME="${CRATES_GITNAME:-mirror}"

cd "$TUNASYNC_WORKING_DIR"

if grep -F -q "$CRATES_PROXY" config.json; then
    exit 0
fi

cat <<EOF > config.json
{
    "dl": "$CRATES_PROXY",
    "api": "https://crates.io/"
}
EOF

git add config.json
git -c user.name="$CRATES_GITNAME" -c user.email="$CRATES_GITMAIL" commit -qm "$CRATES_GITMSG"
