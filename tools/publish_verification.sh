#!/usr/bin/env bash
# Publish the stage-2 verification to the orphan branch analysis-verification (one force-pushed commit).
# Run only from GitHub Actions with contents: write. Usage: tools/publish_verification.sh OUT_DIR
set -euo pipefail
out="${1:?usage: publish_verification.sh OUT_DIR}"
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
d="$(mktemp -d)"
cp "$out"/verification.json "$out"/verification_report.md "$d/"
cat > "$d/README.md" <<EOT
# Проверка на етап 2

Резултат от fogNowcast. Метод: docs/verification.md в основния клон.

> **Изследване, не за оперативна употреба.** Сравнение на суровия ICON-EU, устойчивостта и корекцията с остатък в t0 по срок 0–6 ч.

Модел: ICON-EU, отделни рънове от Open-Meteo Single Runs API. Наблюдения: Национално тол управление (bgtoll.bg), лиценз CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/deed.bg); METAR: Iowa Environmental Mesonet.
EOT
cd "$d"
git init -q
git checkout -q -b analysis-verification
git add -A
git -c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com" \
  commit -q -m "Verification"
git push -q -f "$repo" analysis-verification
echo "published analysis-verification"
