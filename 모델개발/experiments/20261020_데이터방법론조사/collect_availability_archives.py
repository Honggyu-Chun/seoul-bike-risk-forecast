"""Collect public source archives and container metadata only; never inspect outcomes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import re
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEST = ROOT / "data" / "raw" / "research_20261020"
DEST.mkdir(parents=True, exist_ok=True)
CATALOG = "https://data.seoul.go.kr/dataList/OA-22382/F/1/datasetView.do"
html = (HERE / "OA-22382_catalog.html").read_text(encoding="utf-8")
entries = []
for row in re.findall(r'<tr id="fileTr_.*?</tr>', html, re.S):
    name = re.search(r'<span title="([^"]+)"', row)
    seq = re.search(r"downloadFile\('([^']+)'\)", row)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    if name and seq:
        entries.append(dict(name=name.group(1), seq=int(seq.group(1)),
                            advertised_MB=re.sub("<.*?>", "", cells[3]).strip(),
                            modified=re.sub("<.*?>", "", cells[4]).strip()))
(HERE / "catalog_files.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

def collect(entry):
    seq = entry["seq"]
    year = 2024 if 9 <= seq <= 12 else 2025
    quarter = seq - (8 if year == 2024 else 12)
    path = DEST / f"availability_{year}q{quarter}.zip"
    url = f"https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?useCache=false&infId=OA-22382&infSeq=1&seq={seq}"
    info = dict(entry, catalog_url=CATALOG, url=url, file=str(path.relative_to(ROOT)),
                target_values_inspected=False, status="started")
    try:
        if not path.exists():
            partial = path.with_suffix(".zip.partial")
            request = urllib.request.Request(url, headers={"User-Agent": "PProjectResearch/1.0", "Referer": CATALOG})
            size = 0
            with urllib.request.urlopen(request, timeout=45) as response, partial.open("wb") as output:
                info["response_url"] = response.url
                info["content_type"] = response.headers.get("Content-Type")
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(block)
                    if size > 220 * 1024 * 1024:
                        raise ValueError("Archive exceeds 220 MiB research download limit")
                    output.write(block)
            if not zipfile.is_zipfile(partial):
                raise ValueError("Response is not a ZIP archive")
            partial.replace(path)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(block)
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"CRC failure: {bad}")
            members = []
            for item in archive.infolist():
                name = item.filename
                if not item.flag_bits & 0x800:
                    try:
                        name = name.encode("cp437").decode("cp949")
                    except (UnicodeError, LookupError):
                        pass
                members.append(dict(name=name, uncompressed_bytes=item.file_size,
                                    compressed_bytes=item.compress_size, crc32=f"{item.CRC:08x}"))
        info.update(status="verified", bytes=path.stat().st_size, sha256=digest.hexdigest(),
                    zip_crc_passed=True, members=members,
                    received_at_utc=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        info.update(status="error", error=f"{type(exc).__name__}: {exc}")
    print(json.dumps({k: info[k] for k in ("file", "status", "bytes", "error") if k in info}, ensure_ascii=False), flush=True)
    return info

selected = [row for row in entries if row["seq"] in (9, 10, 11, 12, 15, 16)]
results = []
with ThreadPoolExecutor(max_workers=2) as pool:
    for job in as_completed([pool.submit(collect, item) for item in selected]):
        results.append(job.result())
        (HERE / "availability_downloads.json").write_text(json.dumps(sorted(results, key=lambda r: r["seq"]), ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(dict(completed=len(results), verified=sum(r["status"] == "verified" for r in results),
                      outcomes_scored=False, model_training_started=False), ensure_ascii=False), flush=True)
