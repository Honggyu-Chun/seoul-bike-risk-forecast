"""Probe public source URLs without credentials; preserve failure evidence.

No bypass of proxy policy, TLS verification, or authentication is attempted.
This probes catalog pages, NOT the datasets themselves. Only success with an
actual data file/API body can establish data availability.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


def probe(source, output_dir, max_bytes=3_000_000):
    url = source["url"]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS URLs without credentials are supported")
    # Authenticated APIs must use a separate, secret-aware client. Do not place
    # keys in this public catalog, including path-based Seoul API keys.
    record = {
        "source": source["id"], "url": url,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "public_catalog_page_not_dataset", "status": None,
    }
    request = urllib.request.Request(url, headers={"User-Agent": "P-Project-DataAudit/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read(max_bytes + 1)
            truncated = len(body) > max_bytes
            body = body[:max_bytes]
            target = output_dir / (source["id"] + ".body")
            target.write_bytes(body)
            record.update(
                status=response.status, bytes_saved=len(body), truncated=truncated,
                content_type=response.headers.get("Content-Type"),
                sha256=hashlib.sha256(body).hexdigest(), file=str(target),
                outcome="http_response_requires_content_inspection",
            )
    except urllib.error.HTTPError as error:
        record.update(status=error.code, outcome="http_error", error=str(error))
    except urllib.error.URLError as error:
        message = str(error.reason)
        outcome = "proxy_connect_denied" if "Tunnel connection failed: 403" in message else "transport_error"
        record.update(outcome=outcome, error=message)
    except (TimeoutError, OSError) as error:
        record.update(outcome="transport_error", error=str(error))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/sources.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/access_latest.json"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/pages"))
    args = parser.parse_args()
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    sources = json.loads(args.catalog.read_text())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: probe(s, args.raw_dir), sources))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    for result in results:
        print(result["source"], result["outcome"], result["status"])
    # Nonzero when access is blocked; writing a report does not imply success.
    return 0 if all(r["status"] == 200 for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
