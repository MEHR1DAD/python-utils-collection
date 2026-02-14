#!/bin/bash
set -e

# 1. Prepare Branch
echo "🧹 Preparing fresh branch..."
git branch -D gh-pages-reset || true
git checkout --orphan gh-pages-reset
git rm -rf .
git checkout main -- .
git reset

# 2. Restore Critical Data
echo "📥 Downloading latest data from live site..."
mkdir -p backend/data
# Use curly braces to capture exit code 0 even if curl fails, but we want it to work.
# Actually, if curl fails (404), we should create empty array.
download_or_empty() {
    url="$1"
    file="$2"
    echo "Fetching $file..."
    if curl -sL -f "$url" -o "$file"; then
        echo "✅ Loaded $file"
    else
        echo "⚠️ Failed to fetch $file, creating empty default."
        echo "[]" > "$file"
    fi
}

# Metrics might be objects, News are lists.
download_or_empty "https://mehr1dad.github.io/python-utils-collection/data_shard_v.json" "backend/data/data_shard_v.json"
download_or_empty "https://mehr1dad.github.io/python-utils-collection/data_shard_c.json" "backend/data/data_shard_c.json"
download_or_empty "https://mehr1dad.github.io/python-utils-collection/data_shard_h.json" "backend/data/data_shard_h.json"
download_or_empty "https://mehr1dad.github.io/python-utils-collection/data_shard_t.json" "backend/data/data_shard_t.json"
download_or_empty "https://mehr1dad.github.io/python-utils-collection/data_shard_global.json" "backend/data/data_shard_global.json"
download_or_empty "https://mehr1dad.github.io/python-utils-collection/trend_history.json" "backend/data/trend_history.json"
download_or_empty "https://mehr1dad.github.io/python-utils-collection/system_metrics.json" "backend/data/system_metrics.json"

# Net status is special path
mkdir -p backend/data
if curl -sL -f "https://mehr1dad.github.io/python-utils-collection/backend/data/net_status.json" -o "backend/data/net_status.json"; then
    echo "✅ Loaded net_status.json"
else
    echo "{}" > "backend/data/net_status.json"
fi

# 3. Clean Media
echo "🗑️ cleaning media folder..."
rm -rf backend/media
mkdir -p backend/media
echo "0" > backend/media/.placeholder

# 4. Commit and Push
echo "💾 Committing..."
git add .
git commit -m "Emergency Reset: Pruned 7GB media, preserved text data"

echo "🚀 Force Pushing..."
git push -f origin gh-pages-reset:gh-pages

echo "🎉 Done! Repository size should be localized now."
