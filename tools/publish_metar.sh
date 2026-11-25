#!/usr/bin/env bash
# Publish the METAR calibration to the orphan branch analysis-metar (one force-pushed commit).
# Run only from GitHub Actions with contents: write. Usage: tools/publish_metar.sh OUT_DIR
set -euo pipefail
out="${1:?usage: publish_metar.sh OUT_DIR}"
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
d="$(mktemp -d)"
cp "$out"/calibration_metar.json "$out"/calibration_report.md "$out"/metar_*.csv.gz "$d/"
cat > "$d/README.md" <<EOT
# Калибриране на флага за мъгла срещу METAR

Резултат от fogNowcast. Формат и метод: docs/obs_metar_synop.md в основния клон.

> **Изследване, не за оперативна употреба.** Праговете не са проверени на отделен период, случаите са малко и групирани в епизоди, а станциите за LBBG, LBPD и LBGO са на 16–31 км. Влажността на станция не е флаг за мъгла на летището.

METAR/SPECI: Iowa Environmental Mesonet, ASOS архив (https://mesonet.agron.iastate.edu/).
Станции: Национално тол управление (bgtoll.bg), лиценз CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/deed.bg).
EOT
cd "$d"
git init -q
git checkout -q -b analysis-metar
git add -A
git -c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com" \
  commit -q -m "METAR calibration"
git push -q -f "$repo" analysis-metar
echo "published analysis-metar"
