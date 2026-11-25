#!/usr/bin/env bash
# Publish the live NTU files. Run only from GitHub Actions (contents: write), after ingest.ntu_live.
#   site/  -> branch gh-pages          (single commit, force-pushed: history does not grow)
#   fb/    -> branch archive-fallback  (normal commits, one per new hourly state)
set -euo pipefail
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
ident=(-c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com")
stamp="$(date -u +%Y-%m-%dT%H:%MZ)"

publish() {  # dir branch mode(amend|append)
  local dir="$1" branch="$2" mode="$3"
  [ -d "$dir" ] || return 0
  cd "$dir"
  if [ ! -d .git ]; then
    git init -q
    git checkout -q -b "$branch"
    git remote add origin "$repo"
  fi
  if [ "$branch" = gh-pages ]; then touch .nojekyll; fi
  git add -A
  if git diff --cached --quiet; then echo "$branch: nothing to publish"; cd - >/dev/null; return 0; fi
  if [ "$mode" = amend ] && git rev-parse -q --verify HEAD >/dev/null; then
    git "${ident[@]}" commit -q --amend -m "NTU live ${stamp}"
    git push -q -f origin "$branch"
  else
    git "${ident[@]}" commit -q -m "NTU fallback ${stamp}"
    git push -q origin "$branch"
  fi
  echo "$branch: published"
  cd - >/dev/null
}

publish site gh-pages amend
publish fb archive-fallback append
