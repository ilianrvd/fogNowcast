#!/usr/bin/env bash
# Publish the METAR archive to the orphan branch archive-metar (one force-pushed commit, docs/metar_archive.md).
# Run only from GitHub Actions with contents: write. Usage: tools/publish_metar_archive.sh ARCHIVE_DIR
set -euo pipefail
src="${1:?usage: publish_metar_archive.sh ARCHIVE_DIR}"
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
d="$(mktemp -d)"
cp "$src"/manifest.json "$d/"
cp -r "$src"/metar "$d/"
cat > "$d/README.md" <<EOT
# Архив на METAR от IEM

Исторически METAR и SPECI за LBSF, LBBG, LBWN, LBPD и LBGO, от 2022-01-01, за всички проекти. Формат: docs/metar_archive.md в основния клон на fogNowcast.

> **Изследване, не за оперативна употреба.** Личен проект, не е официален продукт на сертифициран доставчик.

Данни: Iowa Environmental Mesonet, ASOS архив (https://mesonet.agron.iastate.edu/).
EOT
cd "$d"
git init -q
git checkout -q -b archive-metar
git add -A
git -c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com" \
  commit -q -m "METAR archive"
git push -q -f "$repo" archive-metar
echo "published archive-metar"
