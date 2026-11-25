"""One-off probe of the NTU live feed. Makes exactly ONE request, writes nothing to the repo.

Run only from GitHub Actions (see .github/workflows/probe-ntu.yml).
Prints status, headers, size and a few records so we can confirm access,
wind units and coordinate precision.
"""
import json
import sys
import urllib.request

BASE = "https://bgtoll.bg/index.php/mto/"
ENDPOINTS = ("data", "temperaturefallback")
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"


def main() -> int:
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "data"
    if endpoint not in ENDPOINTS:
        print(f"unknown endpoint {endpoint!r}, expected one of {ENDPOINTS}")
        return 2
    URL = BASE + endpoint
    print(f"GET {URL}")
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            headers = dict(resp.headers.items())
            raw = resp.read()
    except urllib.error.HTTPError as e:
        print(f"HTTP error: {e.code} {e.reason}")
        print(dict(e.headers.items()))
        print(e.read(500))
        return 1
    except Exception as e:  # noqa: BLE001 - probe, report anything
        print(f"Request failed: {type(e).__name__}: {e}")
        return 1

    print(f"status: {status}")
    print(f"bytes: {len(raw)}")
    for k in ("Content-Type", "Date", "Last-Modified", "Cache-Control", "Server"):
        print(f"{k}: {headers.get(k)}")

    text = raw.decode("utf-8-sig")
    try:
        data = json.loads(text)
    except ValueError:
        print("not JSON; first 3000 chars:")
        print(text[:3000])
        return 0
    items = data if isinstance(data, list) else data.get("data", data)
    print(f"top-level type: {type(data).__name__}")
    if isinstance(items, list):
        print(f"records: {len(items)}")
        for rec in items[:3]:
            print(json.dumps(rec, ensure_ascii=False, indent=2))
        keys = sorted({k for r in items if isinstance(r, dict) for k in r})
        print(f"all keys: {keys}")
    else:
        print(json.dumps(items, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
