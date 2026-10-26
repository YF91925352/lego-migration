"""Download the Rebrickable CSV dumps used as the 'source of truth'.

Data source: Rebrickable (https://rebrickable.com/downloads/), free for personal and
commercial use with acknowledgement. The data is not committed to this repository; each
file is fetched only if it is not already present (Rebrickable allows automated downloads
at most once a day, and forbids scraping its web pages, which this never does).

TLS uses the operating system's trust store (via ``truststore``), so the download works
wherever the browser works, including networks with a proxy or antivirus that inspects
HTTPS. Certificate checking is never switched off.

If downloading is not possible at all, save the ``.csv.gz`` files from
https://rebrickable.com/downloads/ into ``data/raw/`` by hand; they are unpacked here.
"""
import gzip
import shutil
import ssl
import urllib.request
from pathlib import Path

BASE_URL = "https://cdn.rebrickable.com/media/downloads/{name}.csv.gz"
FILES = ["colors", "themes", "part_categories", "parts", "sets", "inventories", "inventory_parts"]


def _ssl_context() -> ssl.SSLContext:
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def _unpack(src: Path, target: Path) -> None:
    tmp = target.with_suffix(".csv.part")
    with gzip.open(src, "rb") as gz, open(tmp, "wb") as out:
        shutil.copyfileobj(gz, out)
    tmp.replace(target)  # atomic: a half-written file is never mistaken for a complete one


def download(raw_dir: Path, force: bool = False) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    ctx = _ssl_context()
    for name in FILES:
        target, gz_file = raw_dir / f"{name}.csv", raw_dir / f"{name}.csv.gz"
        if target.exists() and not force:
            print(f"  {target.name}: already present, skipped")
            continue
        if gz_file.exists():  # downloaded by hand in a browser
            _unpack(gz_file, target)
            print(f"  {target.name}: unpacked from {gz_file.name}")
            continue
        req = urllib.request.Request(BASE_URL.format(name=name), headers={"User-Agent": "lego-migration/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp, open(gz_file.with_suffix(".gz.part"), "wb") as out:
                shutil.copyfileobj(resp, out)
        except OSError as e:
            raise SystemExit(
                f"\nCould not download {name}: {e}\n"
                f"Download the .csv.gz files from https://rebrickable.com/downloads/ in a browser,\n"
                f"put them in {raw_dir}, and run this command again."
            ) from None
        gz_file.with_suffix(".gz.part").replace(gz_file)
        _unpack(gz_file, target)
        print(f"  {target.name}: downloaded")
