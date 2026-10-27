#!/usr/bin/env bash
# 从 zhaoolee/ChineseBQB 拉精选分类到 stickers/chinesebqb/。
# 只拉 41 个分类（约 3250 张、501MB），真人、名人、政治类不拉。
# 用法：bash scripts/fetch-stickers.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/stickers/chinesebqb"

read -r -d '' DIRS <<'EOF' || true
006Hamster_仓鼠🐹BQB
007Tiger_胖虎🐯BQB
008HappyDuck_开心鸭🐥BQB
009KumamotoBear_熊本熊🐻BQB
010Cat_是喵星人啦🐱BQB
011Dog_狗🐶BQB
012Parrot_鹦鹉🐦BQB
013PigPecs_小猪佩奇👑BQB
014Pig_猪🐖BQB
015Golden_Curator_Panda金馆长熊猫🐼BQB
017Playing_演奏🎻BQB
018WangEgg_汪蛋🥚BQB
019Green_Hat绿帽子🖼BQB
020TATANBQB
023Emoji_表情符号BQB
026Chicken_小幺鸡🐔BQB
027Turtle_乌龟🐢BQB
029Pigeon_鸽子🕊BQB
031Penguin_沙雕企鹅🐧BQB
033Eat_吃🍔BQB
034WhiteVillain_白色小人👶🏻BQB
035TomAndJerry_猫和老鼠BQB
036Pikachu_皮卡丘BQB
043Altman_奥特曼BQB
045MatchstickMen_火柴人BQB
046WhatToWear_穿什么BQB
048SpongeBob_海绵宝宝BQB
049CatEveryday_猫咪日常BQB
052Squirtle_杰尼龟BQB
056Doraemon_哆啦A梦BQB
060MurCat_Mur猫😺BQB
065TravelFrog_旅行青蛙🐸BQB
083ChineseNewYear_中国春节_BQB
090SailorMoon_美少女战士_BQB
095GenShin_原神_BQB
098spyxfamily_间谍过家家_BQB
106_Frieren_芙莉莲🪄_BQB
107_FLittleBrother_葫芦兄弟_BQB
108_Pingu_企鹅家族_BQB
109_Opossum_负鼠_BQB
110_AI_人工智能_BQB
EOF

mkdir -p "$DEST"
cd "$DEST"

if [ ! -d .git ]; then
  git clone --filter=blob:none --no-checkout --depth 1 \
    https://github.com/zhaoolee/ChineseBQB.git .
fi
git sparse-checkout init --cone
# shellcheck disable=SC2086
git sparse-checkout set $DIRS
git checkout
git sparse-checkout list | wc -l | xargs echo "分类数："
find . -type f \( -iname '*.gif' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) | wc -l | xargs echo "图片数："
echo "完成。接下来跑：python3 scripts/build-animated-index.py"
