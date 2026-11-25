#!/usr/bin/env bash
# Publish the correction result to the orphan branch analysis-correction (one force-pushed commit).
# Run only from GitHub Actions with contents: write. Usage: tools/publish_correction.sh OUT_DIR
set -euo pipefail
out="${1:?usage: publish_correction.sh OUT_DIR}"
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
d="$(mktemp -d)"
cp "$out/correction_by_station.json" "$out/correction_report.md" "$d/"
cat > "$d/README.md" <<EOT
# Корекция на модела по станции

Резултат от fogNowcast. Формат и метод: docs/correction_model.md в основния клон.

Наблюдения: Национално тол управление (bgtoll.bg), лиценз CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/deed.bg). Модел: ICON-EU чрез Open-Meteo Historical Forecast.
EOT
cd "$d"
git init -q
git checkout -q -b analysis-correction
git add -A
git -c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com" \
  commit -q -m "Correction by station"
git push -q -f "$repo" analysis-correction
echo "published analysis-correction"
