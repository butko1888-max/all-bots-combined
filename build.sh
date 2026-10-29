#!/usr/bin/env bash
set -e
echo "═══ Preparing folders ═══"
mkdir -p glowroom rodychi_leads rdch_bonus
touch glowroom/__init__.py rodychi_leads/__init__.py rdch_bonus/__init__.py
echo "═══ Fetching glowroom-bot code ═══"
rm -rf /tmp/gr
git clone --depth 1 https://github.com/butko1888-max/glowroom-bot.git /tmp/gr
cp /tmp/gr/bot.py        glowroom/bot.py
cp /tmp/gr/database.py   glowroom/database.py
cp /tmp/gr/admin.py      glowroom/admin.py
cp /tmp/gr/notify_bot.py glowroom/notify_bot.py
cp /tmp/gr/run.py        glowroom/run.py
# Image resource (studio_directions.jpg) used by run.py — copy too
if [ -f /tmp/gr/studio_directions.jpg ]; then
  cp /tmp/gr/studio_directions.jpg glowroom/studio_directions.jpg
fi
touch glowroom/__init__.py
echo "═══ Installing deps ═══"
pip install -r requirements.txt
echo "═══ Build done ═══"
