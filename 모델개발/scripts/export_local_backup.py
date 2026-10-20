"""Export the complete project snapshot for local continuation, without caches."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables" / "따릉이_전체작업_로컬이전_20261020.zip"
MANIFEST = ROOT / "reports" / "local_transfer_manifest.json"
EXCLUDED_DIRS = {".git", ".venv", ".cache", ".pytest_cache", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache"}


def excluded(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return True
    if path in {OUT, MANIFEST, Path(str(OUT) + ".sha256"), OUT.with_suffix(".zip.partial")}:
        return True
    return (
        path.name == ".env"
        or path.name.startswith(".env.")
        or path.name in {"id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc"}
        or path.suffix in {".pyc", ".pem", ".key"}
    )


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if excluded(path) or not path.is_file():
            continue
        if path.is_symlink():
            raise RuntimeError(f"Inspect symlink before exporting: {path.relative_to(ROOT)}")
        files.append(path)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "/workspace/P_Project",
        "archive_root": "P_Project/",
        "scope": "All current project files, raw and processed data, research/replay models, logs and deliverables; excludes environment/cache/Git metadata/secrets.",
        "excluded_directory_names": sorted(EXCLUDED_DIRS),
        "not_transferred": ["running processes", "account usage allowance", "full chat history", "files outside this project"],
        "files": {},
    }
    for path in files:
        manifest["files"][str(path.relative_to(ROOT))] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    files.append(MANIFEST)
    total = sum(path.stat().st_size for path in files)
    print(json.dumps({"stage": "compressing", "files": len(files), "source_bytes": total}), flush=True)
    partial = OUT.with_suffix(".zip.partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
        for i, path in enumerate(files, 1):
            archive.write(path, "P_Project/" + str(path.relative_to(ROOT)))
            if i % 100 == 0:
                print(json.dumps({"stage": "compressing", "done": i, "total": len(files)}), flush=True)
    print(json.dumps({"stage": "verifying_archive"}), flush=True)
    with zipfile.ZipFile(partial) as archive:
        assert archive.testzip() is None, "ZIP CRC failure"
        for relative, expected in manifest["files"].items():
            with archive.open("P_Project/" + relative) as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected["sha256"]:
                raise RuntimeError(f"Archived file hash mismatch: {relative}")
    partial.replace(OUT)
    Path(str(OUT) + ".sha256").write_text(digest(OUT) + "  " + OUT.name + "\n")
    print(json.dumps({"stage": "verified", "path": str(OUT), "bytes": OUT.stat().st_size, "files": len(files)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
