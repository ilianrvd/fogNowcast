"""Health check of the live NTU pipeline. Read-only: Pages and branch dates, no request to NTU.

Run hourly from GitHub Actions (.github/workflows/health.yml, triggered by cron-job.org). Prints the
problems as JSON lines to stdout and writes a Markdown report to --report; the workflow opens, updates or
closes the issue "fogNowcast: данните са спрели". Exit code 0 in both cases (the issue is the signal).
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

UTC = timezone.utc
PAGES = "https://ilianrvd.github.io/fogNowcast/obs/"
UA = "fogNowcast-health/0.1 (+https://github.com/ilianrvd/fogNowcast)"
MAX_FILE_AGE_MIN = 45      # 15-min cadence + Pages cache (10 min) + one missed run
MAX_BRANCH_AGE_MIN = 60
MIN_STATIONS = 50          # ~95 normally
MAX_SHARE = {"late": 0.5, "fallback_unknown": 0.5, "t_frozen": 0.2}


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def age_min(now, ts):
    return None if not ts else round((now - parse(ts)).total_seconds() / 60)


def evaluate(now, latest, state, branch_times):
    """Return a list of problems (strings). latest/state may be None (could not be read)."""
    p = []
    if latest is None:
        p.append("ntu_latest.json не може да се прочете от Pages")
    else:
        a = age_min(now, latest.get("generated_at"))
        if a is None or a > MAX_FILE_AGE_MIN:
            p.append(f"ntu_latest.json е на {a} мин (праг {MAX_FILE_AGE_MIN})")
        st = latest.get("stations") or []
        if len(st) < MIN_STATIONS:
            p.append(f"само {len(st)} станции в ntu_latest.json (праг {MIN_STATIONS})")
        for flag, lim in MAX_SHARE.items():
            n = sum(1 for s in st if flag in (s.get("qc") or {}).get("flags", []))
            if st and n / len(st) > lim:
                p.append(f"{n} от {len(st)} станции с флаг {flag} (праг {int(lim * 100)} %)")
    if state is None:
        p.append("ntu_fetch_state.json не може да се прочете от Pages")
    else:
        a = age_min(now, state.get("last_request_at"))
        if a is None or a > MAX_FILE_AGE_MIN:
            p.append(f"последната заявка към НТУ е преди {a} мин: изпълненията може да са спрели (праг {MAX_FILE_AGE_MIN})")
    for br, ts in sorted(branch_times.items()):
        a = age_min(now, ts)
        if a is None or a > MAX_BRANCH_AGE_MIN:
            p.append(f"клон {br} не е обновяван от {a} мин (праг {MAX_BRANCH_AGE_MIN})")
    return p


def http_json(url, token=None):
    h = {"User-Agent": UA, "Accept": "application/json", "Cache-Control": "no-cache"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.loads(r.read().decode("utf-8-sig"))


def safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        print(f"read failed: {a[0]}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "ilianrvd/fogNowcast"))
    a = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    now = datetime.now(UTC)
    latest = safe(http_json, PAGES + "ntu_latest.json")
    state = safe(http_json, PAGES + "ntu_fetch_state.json")
    branches = {}
    for br in ("archive-live", "archive-fallback"):
        d = safe(http_json, f"https://api.github.com/repos/{a.repo}/branches/{br}", token)
        branches[br] = (d or {}).get("commit", {}).get("commit", {}).get("committer", {}).get("date")
    problems = evaluate(now, latest, state, branches)
    lines = [f"Проверка: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}", ""]
    lines += [f"- {x}" for x in problems] or ["Всичко е наред."]
    with open(a.report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"ok={'true' if not problems else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
