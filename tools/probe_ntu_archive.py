"""One-off probe of the NTU open-data archive on Dropbox (read-only, nothing written to the repo).

Run only from GitHub Actions (see .github/workflows/probe-ntu-archive.yml).

The public folder page lists files via client-side JS, so file names are not in the HTML.
Instead we stream the whole folder download (dl=1, ~230 MB) once, list all entries and
print the structure of the first and the last daily file. Nothing is saved.
"""
import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile

FOLDER = ("https://www.dropbox.com/scl/fo/3v3asm04vcf3oahhgb39w/AJ8-3EDvwDf7d3a6OGir1wo"
          "?rlkey=i95zfngdxye3lj6zwrk97cgg5&st=4pyajkii&dl=1")
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"


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
    # The outer zip stores inner zips uncompressed with data descriptors, which cannot be
    # streamed. Download it to a temp file (outside the repo) and read the central directory.
    path = os.path.join(tempfile.mkdtemp(), "meteo.zip")
    req = urllib.request.Request(FOLDER, headers={"User-Agent": UA})
    total = 0
    with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as out:
        print(f"status={resp.status} type={resp.headers.get('Content-Type')} "
              f"length={resp.headers.get('Content-Length')}")
        for b in chunks(resp):
            out.write(b)
            total += len(b)
    print(f"downloaded {total} bytes")
    z = zipfile.ZipFile(path)
    infos = z.infolist()
    files = [i for i in infos if not i.is_dir()]
    print(f"entries: {len(infos)} (dirs {len(infos) - len(files)}, files {len(files)})")
    print("dirs:", [i.filename for i in infos if i.is_dir()])
    print("first files:", [i.filename for i in files[:6]])
    print("last files:", [i.filename for i in files[-4:]])
    by_dir = {}
    for i in files:
        d = i.filename.rsplit("/", 1)[0]
        by_dir[d] = by_dir.get(d, 0) + 1
    print("files per folder:", by_dir)
    sizes = [i.file_size for i in files]
    print(f"file sizes: min={min(sizes)} max={max(sizes)} mean={sum(sizes) // len(sizes)}")
    for label, info in (("FIRST", files[0]), ("LAST", files[-1])):
        print(f"--- {label}: {info.filename} ({info.file_size} bytes)")
        blob = z.read(info)
        if zipfile.is_zipfile(io.BytesIO(blob)):
            describe(blob)
        else:
            print("not a zip; first 1200 bytes:")
            print(blob[:1200].decode("utf-8", "replace"))
    os.remove(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
