"""One-off probe of the NTU open-data archive on Dropbox (read-only, nothing written to the repo).

Run only from GitHub Actions (see .github/workflows/probe-ntu-archive.yml).
Step 1: fetch the public folder page once and list the archive file names/links it exposes.
Step 2: download ONE daily zip (size-capped) and print its structure and a sample record.
"""
import io
import json
import re
import sys
import urllib.request
import zipfile

FOLDER = ("https://www.dropbox.com/scl/fo/3v3asm04vcf3oahhgb39w/AJ8-3EDvwDf7d3a6OGir1wo"
          "?rlkey=i95zfngdxye3lj6zwrk97cgg5&st=4pyajkii&dl=0")
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"
MAX_BYTES = 40 * 1024 * 1024


def get(url: str, limit: int | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(limit + 1) if limit else r.read()
        return r.status, dict(r.headers.items()), data


def main() -> int:
    status, headers, page = get(FOLDER, limit=5 * 1024 * 1024)
    text = page.decode("utf-8", "replace")
    print(f"folder page: status={status} bytes={len(page)} type={headers.get('Content-Type')}")
    names = sorted(set(re.findall(r"meteoData_\d{4}-\d{2}-\d{2}\.zip", text)))
    print(f"daily zip names on page: {len(names)}")
    print(names[:5], "...", names[-3:])
    links = sorted(set(re.findall(r"https://www\.dropbox\.com/scl/fi/[A-Za-z0-9]+/[^\"'\\\s<>]*meteoData_[^\"'\\\s<>]+", text)))
    links = [l.replace("\\u0026", "&").replace("\\/", "/") for l in links]
    print(f"file links on page: {len(links)}")
    for l in links[:3]:
        print(l)
    if not links:
        print("no per-file links found; first 1500 chars of page text:")
        print(re.sub(r"\s+", " ", text)[:1500])
        return 1

    url = re.sub(r"dl=0", "dl=1", links[-1]) if "dl=0" in links[-1] else links[-1] + "&dl=1"
    status, headers, blob = get(url, limit=MAX_BYTES)
    print(f"download: status={status} bytes={len(blob)} type={headers.get('Content-Type')}")
    if len(blob) > MAX_BYTES:
        print("file larger than cap, stopping")
        return 1
    z = zipfile.ZipFile(io.BytesIO(blob))
    members = z.namelist()
    print(f"zip members: {len(members)}")
    print(members[:5], "...", members[-3:])
    first = z.read(members[0]).decode("utf-8-sig")
    print(f"first member size: {len(first)} chars")
    data = json.loads(first)
    if isinstance(data, list):
        print(f"records in first member: {len(data)}")
        print(json.dumps(data[:2], ensure_ascii=False, indent=2))
        print("keys:", sorted({k for r in data if isinstance(r, dict) for k in r}))
    else:
        print(json.dumps(data, ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
