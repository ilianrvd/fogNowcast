"""One-off probe of the NTU open-data archive on Dropbox (read-only, nothing written to the repo).

Run only from GitHub Actions (see .github/workflows/probe-ntu-archive.yml).

The public folder page lists files via client-side JS, so file names are not in the HTML.
Instead we stream the whole folder download (dl=1, ~230 MB) once, list all entries and
print the structure of the first and the last daily file. Nothing is saved.
"""
import io
import json
import sys
import urllib.request
import zipfile

from stream_unzip import stream_unzip

FOLDER = ("https://www.dropbox.com/scl/fo/3v3asm04vcf3oahhgb39w/AJ8-3EDvwDf7d3a6OGir1wo"
          "?rlkey=i95zfngdxye3lj6zwrk97cgg5&st=4pyajkii&dl=1")
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"
MAX_ENTRY_BYTES = 40 * 1024 * 1024


def chunks(resp):
    while True:
        b = resp.read(1 << 16)
        if not b:
            return
        yield b


def describe(blob: bytes) -> None:
    z = zipfile.ZipFile(io.BytesIO(blob))
    members = z.namelist()
    print(f"  inner zip members: {len(members)}")
    print(f"  first/last: {members[:3]} ... {members[-2:]}")
    raw = z.read(members[0]).decode("utf-8-sig")
    print(f"  first member chars: {len(raw)}")
    data = json.loads(raw)
    if isinstance(data, list):
        print(f"  records in first member: {len(data)}")
        print(json.dumps(data[:2], ensure_ascii=False, indent=2))
        print("  keys:", sorted({k for r in data if isinstance(r, dict) for k in r}))
    else:
        print(json.dumps(data, ensure_ascii=False)[:1500])


def main() -> int:
    req = urllib.request.Request(FOLDER, headers={"User-Agent": UA})
    names = []
    first_blob = last_blob = None
    first_name = last_name = None
    with urllib.request.urlopen(req, timeout=300) as resp:
        print(f"status={resp.status} type={resp.headers.get('Content-Type')} "
              f"length={resp.headers.get('Content-Length')}")
        for name, size, parts in stream_unzip(chunks(resp)):
            name = name.decode("utf-8", "replace")
            is_dir = name.endswith("/")
            keep = None
            if not is_dir:
                buf = io.BytesIO()
                got = 0
                for part in parts:
                    got += len(part)
                    if got <= MAX_ENTRY_BYTES:
                        buf.write(part)
                keep = buf.getvalue()
            else:
                for _ in parts:
                    pass
            names.append((name, size))
            if not is_dir:
                if first_blob is None:
                    first_blob, first_name = keep, name
                last_blob, last_name = keep, name
    files = [n for n, _ in names if not n.endswith("/")]
    dirs = [n for n, _ in names if n.endswith("/")]
    print(f"entries: {len(names)} (dirs {len(dirs)}, files {len(files)})")
    print("dirs:", dirs)
    print("first files:", files[:6])
    print("last files:", files[-4:])
    by_month = {}
    for n in files:
        by_month[n.rsplit("/", 1)[0]] = by_month.get(n.rsplit("/", 1)[0], 0) + 1
    print("files per folder:", by_month)
    for label, nm, blob in (("FIRST", first_name, first_blob), ("LAST", last_name, last_blob)):
        print(f"--- {label}: {nm} ({len(blob) if blob else 0} bytes)")
        if blob and zipfile.is_zipfile(io.BytesIO(blob)):
            describe(blob)
        elif blob:
            print("not a zip; first 1200 bytes:")
            print(blob[:1200].decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
