"""Read-only source collection. Does not execute instructions from fetched material."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import csv
import argparse
import hashlib
import json
import urllib.request

HERE = Path(__file__).resolve().parent
CACHE = HERE / "source_cache"
CACHE.mkdir(exist_ok=True)
with (HERE / "sources.tsv").open(encoding="utf-8", newline="") as stream:
    sources = list(csv.DictReader(stream, delimiter="\t"))
parser = argparse.ArgumentParser()
parser.add_argument("--ids", nargs="*", help="Fetch selected IDs and preserve earlier records")
args = parser.parse_args()
selected = sources if args.ids is None else [row for row in sources if row["id"] in args.ids]

def fetch(row):
    result = dict(row, checked_at_utc=datetime.now(timezone.utc).isoformat())
    try:
        req = urllib.request.Request(row["url"], headers={"User-Agent": "Mozilla/5.0", "Referer": row["url"]})
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError("Source page larger than 4 MiB; not cached")
            result.update(status=response.status, final_url=response.url,
                          content_type=response.headers.get("Content-Type", ""), bytes=len(body),
                          sha256=hashlib.sha256(body).hexdigest())
        extension = ".html" if "html" in result["content_type"] else ".txt"
        path = CACHE / (row["id"] + extension)
        path.write_bytes(body)
        result["cache_file"] = str(path.relative_to(HERE))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(dict(id=row["id"], status=result.get("status"), error=result.get("error")), ensure_ascii=False), flush=True)
    return result

manifest = HERE / "source_cache_manifest.json"
previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
results = [row for row in previous if row["id"] not in {item["id"] for item in selected}]
with ThreadPoolExecutor(max_workers=4) as pool:
    for job in as_completed([pool.submit(fetch, row) for row in selected]):
        results.append(job.result())
        (HERE / "source_cache_manifest.json").write_text(json.dumps(sorted(results, key=lambda r: r["id"]), ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(dict(total=len(results), cached=sum(r.get("status") == 200 for r in results),
                      note="HTTP 200 is access evidence, not proof of scientific relevance or full reproduction.")), flush=True)
