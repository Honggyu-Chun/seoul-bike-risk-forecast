"""Download a bounded public file, preserving URL, hash, and partial/full scope.

For public file URLs only. Never place API keys in the manifest. HTTPS and
certificate verification remain enabled. Existing matching files are reused.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


def fetch(entry, raw_dir):
    target = raw_dir / entry["file"]
    metadata_path = target.with_name(target.name + ".meta.json")
    if target.exists():
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if metadata.get("url") == entry["url"] and metadata.get("form") == entry.get("form") and metadata.get("sha256") == digest and metadata.get("truncated", False) == bool(entry.get("sample_bytes")):
                print("Reused verified local bytes:", target)
                return metadata
        raise FileExistsError(f"Preserve existing file; inspect before replacement: {target}")
    parsed = urllib.parse.urlsplit(entry["url"])
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS without credentials")
    form = entry.get("form")
    request = urllib.request.Request(entry["url"], data=urllib.parse.urlencode(form).encode() if form else None,
                                    headers={"User-Agent": "P-Project-DataAudit/0.1", "Referer": entry.get("referer", "https://data.seoul.go.kr/")})
    limit = entry.get("sample_bytes", entry.get("max_bytes", 30_000_000))
    partial = target.with_name(target.name + ".part")
    record = {"url": entry["url"], "form": form, "file": str(target),
              "retrieved_utc": datetime.now(timezone.utc).isoformat()}
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as stream:
            record.update(status=response.status, content_type=response.headers.get("Content-Type"),
                          content_length=response.headers.get("Content-Length"),
                          content_disposition=response.headers.get("Content-Disposition"))
            total = 0
            while total <= limit:
                chunk = response.read(min(1024 * 1024, limit + 1 - total))
                if not chunk:
                    break
                stream.write(chunk)
                total += len(chunk)
        truncated = total > limit
        if truncated and not entry.get("sample_bytes"):
            raise ValueError("Response exceeds declared maximum; retained .part, not a complete dataset")
        if truncated:
            body = partial.read_bytes()[:limit]
            if target.suffix == ".csv":
                body = body[:body.rfind(b"\n") + 1]
            partial.write_bytes(body)
        with partial.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        record.update(bytes=partial.stat().st_size, truncated=truncated, sha256=digest)
        # A byte download still requires format/semantic validation afterwards.
        partial.rename(target)
        metadata_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(record, ensure_ascii=False))
        return record
    except Exception:
        # A partial download is retained for diagnosis and never marked complete.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/downloads.json"))
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    entries = json.loads(args.manifest.read_text())
    if args.only:
        unknown = set(args.only) - {entry["id"] for entry in entries}
        if unknown:
            parser.error("Unknown IDs: " + str(sorted(unknown)))
    for entry in entries:
        if not args.only or entry["id"] in args.only:
            fetch(entry, args.raw_dir)


if __name__ == "__main__":
    main()
