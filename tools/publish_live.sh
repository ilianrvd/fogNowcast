#!/usr/bin/env bash
# Publish the live NTU files. Run only from GitHub Actions (contents: write), after ingest.ntu_live.
#   site/  -> branch gh-pages          (single commit, force-pushed: history does not grow)
#   fb/    -> branch archive-fallback  (normal commits, one per new hourly state)
#   live/  -> branch archive-live      (single commit, force-pushed, only after a row-count check)
set -euo pipefail
repo="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
ident=(-c user.name="fogNowcast Actions" -c user.email="actions@users.noreply.github.com")
stamp="$(date -u +%Y-%m-%dT%H:%MZ)"

rows() { zcat "$1" 2>/dev/null | wc -l; }

# Refuse to replace the archive commit if any file that was already archived has fewer rows (or is gone).
# This is the only place where the live archive could lose data, so it fails the run instead.
check_rows() {
  local f old new
  git rev-parse -q --verify HEAD >/dev/null || return 0
  for f in $(git ls-tree --name-only HEAD); do
    case "$f" in *.csv.gz) ;; *) continue ;; esac
    old="$(git show "HEAD:$f" | zcat | wc -l)"
    [ -f "$f" ] || { echo "archive-live: $f missing locally, not publishing"; return 1; }
    new="$(rows "$f")"
    if [ "$new" -lt "$old" ]; then echo "archive-live: $f has $new rows < $old, not publishing"; return 1; fi
  done
}

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
  if [ "$branch" = archive-live ]; then check_rows || { cd - >/dev/null; return 1; }; fi
  # explicit checks: `set -e` does not apply inside a function called as `publish ... || status=1`
  local ok=1
  if [ "$mode" = amend ] && git rev-parse -q --verify HEAD >/dev/null; then
    git "${ident[@]}" commit -q --amend -m "NTU ${branch} ${stamp}" && git push -q -f origin "$branch" || ok=0
  else
    git "${ident[@]}" commit -q -m "NTU ${branch} ${stamp}" && git push -q origin "$branch" || ok=0
  fi
  if [ "$ok" = 0 ]; then echo "$branch: publishing failed"; cd - >/dev/null; return 1; fi
  echo "$branch: published"
  cd - >/dev/null
}

status=0
publish site gh-pages amend || status=1
publish fb archive-fallback append || status=1
publish live archive-live amend || status=1
exit $status
