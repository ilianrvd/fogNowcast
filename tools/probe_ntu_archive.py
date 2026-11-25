"""One-off probe of the NTU open-data archive on Dropbox (read-only, nothing written to the repo).

Run only from GitHub Actions (see .github/workflows/probe-ntu-archive.yml).

The public folder page lists files via client-side JS, so file names are not in the HTML.
Instead we stream the folder download (dl=1) as a zip and read only its first few entries,
then close the connection. Prints names, sizes and the structure of the first daily zip.
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
MAX_ENTRIES = 4
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        print(f"status={resp.status} type={resp.headers.get('Content-Type')} "
              f"length={resp.headers.get('Content-Length')} "
              f"disposition={resp.headers.get('Content-Disposition')}")
        first_blob = None
        for i, (name, size, parts) in enumerate(stream_unzip(chunks(resp))):
            name = name.decode("utf-8", "replace")
            if i >= MAX_ENTRIES:
                for _ in parts:
                    pass
                break
            got = 0
            buf = io.BytesIO() if i == 0 else None
            for part in parts:
                got += len(part)
                if buf is not None and got <= MAX_ENTRY_BYTES:
                    buf.write(part)
                if got > MAX_ENTRY_BYTES and i != 0:
                    break
            print(f"entry {i}: {name} declared_size={size} read={got}")
            if i == 0:
                first_blob = buf.getvalue()
        if first_blob and zipfile.is_zipfile(io.BytesIO(first_blob)):
            print("first entry is a zip:")
            describe(first_blob)
        elif first_blob:
            print("first entry is not a zip; first 800 bytes:")
            print(first_blob[:800].decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
