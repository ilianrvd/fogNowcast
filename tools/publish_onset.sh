#!/usr/bin/env bash
# Publish the fog onset check to the orphan branch analysis-onset (one force-pushed commit).
# Run only from GitHub Actions with contents: write. Usage: tools/publish_onset.sh OUT_DIR
set -euo pipefail
out="${1:?usage: publish_onset.sh OUT_DIR}"
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
d="$(mktemp -d)"
cp "$out"/onset.json "$out"/onset_report.md "$d/"
cat > "$d/README.md" <<EOT
# Поява на мъгла на летищата

Резултат от fogNowcast. Метод: docs/fog_onset.md в основния клон.

> **Изследване, не за оперативна употреба.**

METAR: Iowa Environmental Mesonet. Модел: ICON-EU, отделни рънове от Open-Meteo Single Runs API. Наблюдения: Национално тол
управление (bgtoll.bg), лиценз CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/deed.bg).
EOT
cd "$d"
git init -q
git checkout -q -b analysis-onset
git add -A
git -c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com" commit -q -m "Fog onset check"
git push -q -f "$repo" analysis-onset
echo "published analysis-onset"
