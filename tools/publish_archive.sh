#!/usr/bin/env bash
# Publish the normalised NTU archive: one orphan branch archive-YYYY-MM per month.
# Run only from GitHub Actions with contents: write. Usage: tools/publish_archive.sh OUT_DIR
set -euo pipefail
out="${1:?usage: publish_archive.sh OUT_DIR}"
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
for f in "$out"/ntu_*-*.csv.gz; do
  month="$(basename "$f" .csv.gz)"; month="${month#ntu_}"
  branch="archive-${month}"
  d="$(mktemp -d)"
  cp "$f" "$d/"
  cp "$out/ntu_stations.json" "$out/ntu_import_meta.json" "$d/"
  cat > "$d/README.md" <<EOT
# NTU archive ${month}

Нормализирани данни от пътните метеостанции на Национално тол управление.

Данни: Национално тол управление (bgtoll.bg), лиценз CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/deed.bg). Обработка: fogNowcast.
Формат: docs/archive_ntu.md в основния клон на fogNowcast.
EOT
  (
    cd "$d"
    git init -q
    git checkout -q -b "$branch"
    git add -A
    git -c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com" \
      commit -q -m "NTU archive ${month}"
    git push -q -f "$repo" "$branch"
  )
  echo "published $branch"
done
