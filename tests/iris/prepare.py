"""Download only the hashed Linux amd64 test inputs; never run them on the host."""
import hashlib
import json
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[2]
cache = root / ".iris-test" / "downloads"
cache.mkdir(parents=True, exist_ok=True)
inputs = json.loads((root / "tests/iris/inputs.lock.json").read_text())
for name, item in inputs.items():
    if not isinstance(item, dict):
        continue
    path = cache / item["url"].rsplit("/", 1)[-1]
    if not path.exists():
        with urllib.request.urlopen(item["url"], timeout=60) as response, path.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != item["sha256"]:
        raise SystemExit(f"Checksum mismatch: {name}")
    print(f"Verified {name} {item['version']}")
