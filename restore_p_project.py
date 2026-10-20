"""Join the verified project parts; optionally extract into a new folder.

Keep this file beside backup_parts/, then run:
    python restore_p_project.py --extract
Requires only Python 3.9+ standard library.
"""

import argparse
import hashlib
from pathlib import Path
import tempfile
import zipfile


PARTS = [('p_project_full.zip.001', 50331648, '1894e9cc3bd9d6cbe61a5830e2a66713ccd124dde2f93e653f01317e045c6246'), ('p_project_full.zip.002', 50331648, '79c73f65c739bd4c4588e004b7476e9b5ba398fa7d7ad7832f52ad8f9c509d62'), ('p_project_full.zip.003', 50331648, '64c076d3baba830dfee0632a2afcbd2770414e92ad9875b9c473223d98be202e'), ('p_project_full.zip.004', 50331648, 'fa75903d363ae992afb4fab5504edd06a39ab333efdeb049e3bce8697303a7ce'), ('p_project_full.zip.005', 50331648, 'c2c62951a4f27bd86f581fa502739fcb7969577edd2060a9165fd38164435d4f'), ('p_project_full.zip.006', 50331648, 'ff44d89f983235178170b5eb82d909d185fc18bae69670d3f37fb20387734b97'), ('p_project_full.zip.007', 50331648, '0df22ba57757c7584770564c7914d77e045a259b0a5a1a1f2884fe4c5a09f170'), ('p_project_full.zip.008', 50331648, '0f6c81f8f6ee790adbea7a5337869cd5fc0dc1371b324045df439fede02e8f2a'), ('p_project_full.zip.009', 50331648, '0efd9b0e813f5411bb90e143a550e14bc3ff9ecbe023657d67bcea77bd046a6b'), ('p_project_full.zip.010', 50331648, '272fe72cbd339d8834ac071f870f036982e502b91d80b637c6499140aa83e6e7'), ('p_project_full.zip.011', 50331648, 'bf3fe8b619cec988841115787a96c38533496d4aa61c5ef10355485cee7507bb'), ('p_project_full.zip.012', 50331648, '64fa14590892ff973efff38751183fe0a3152946c925efd8a9b6953b4adce641'), ('p_project_full.zip.013', 50331648, 'b78fbdad4c4949026ab4db035f53c80540a257d11e2220f1eb2d8d1b49b4b9c6'), ('p_project_full.zip.014', 50331648, '094a8e80e8f4827f241e2e55b893049feb5c59b2689cf385344bbf9669cc305a'), ('p_project_full.zip.015', 50331648, '82052449b9f638b8211ae99bb972db8b0580ad6df4affbd7a874ead20c498fac'), ('p_project_full.zip.016', 50331648, 'da9f4a7763112dc88191ee3e73090ee71f113b7ac5cf7047f7f81075fb716fa2'), ('p_project_full.zip.017', 50331648, '40b7f6df22c73201e8a63cb948380b7f16c36527436a9a1dc7ef11409e9aae4e'), ('p_project_full.zip.018', 50331648, '6d1c110c78a4ff55875501ace3e2784ac6e959bca38b3cfae397a4787125634e'), ('p_project_full.zip.019', 50331648, '8e1d9a123fb4cdc10bbfd2600417c9f9030a93fff761d8ea10008dcf3496d9a9'), ('p_project_full.zip.020', 50331648, '72309dcb7cca0b18db97e396f3f55fcc23e4d3c4a7b315bd429eb129dd8c1b86'), ('p_project_full.zip.021', 50331648, '832128c6d63f6a639d30eed421eef2b438c65e8ed8ba33354a40ae47eb10d981'), ('p_project_full.zip.022', 50331648, '979e2ccd08e823777e3b458d2a44ad8b1e27d355d9044d3847c8f663b3a5a048'), ('p_project_full.zip.023', 50331648, '8fb5ff9957b4bf7b24add30d89d5819e36d1826fb40e3de72f7fc19fdaaceb6b'), ('p_project_full.zip.024', 50331648, '7e5be442b95c734c3af466cf51bd752e2c34546a0b95e519d7ec205a14033d7b'), ('p_project_full.zip.025', 50331648, '5775ae1cfaa1131fe6866ded2eeb8dd216ea744eeab907dfc24c8f8d745b8378'), ('p_project_full.zip.026', 50331648, '3954d2ed5fae1a5b012b722a154d4081f8d48b3881a771fd889e37780e0e32c5'), ('p_project_full.zip.027', 5049010, '894cbfb0575f525b607a66152faae5ee058a056f3f020faa7de19a14dad502d8')]

ARCHIVE_SHA256 = "3f153b2e5f77103c317b8adfc236ffbeffd7336451bbfff0eda95c9e76962abc"


def file_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    parts_dir = Path(__file__).resolve().parent / "backup_parts"
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    archive_path = out / "P_Project_full.zip"
    project = out / "P_Project"
    if args.extract and project.exists():
        raise SystemExit("P_Project already exists. Choose another --output-dir; existing files will not be overwritten.")
    invalid = [name for name, size, _ in PARTS if not (parts_dir / name).is_file() or (parts_dir / name).stat().st_size != size]
    if invalid:
        raise SystemExit("Missing or incomplete downloads: " + ", ".join(invalid))
    if archive_path.exists():
        if file_hash(archive_path) != ARCHIVE_SHA256:
            raise SystemExit("P_Project_full.zip already exists with different contents. Choose another --output-dir.")
        print("Existing combined ZIP matches the expected SHA-256.", flush=True)
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=out, prefix="p_project_restore_", suffix=".partial", delete=False) as destination:
                temporary = Path(destination.name)
                whole = hashlib.sha256()
                for name, _, expected in PARTS:
                    part_hash = hashlib.sha256()
                    with (parts_dir / name).open("rb") as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            destination.write(block)
                            part_hash.update(block)
                            whole.update(block)
                    if part_hash.hexdigest() != expected:
                        raise SystemExit("Checksum mismatch. Download again: " + name)
                    print("Verified " + name, flush=True)
                if whole.hexdigest() != ARCHIVE_SHA256:
                    raise SystemExit("Combined ZIP checksum mismatch.")
            temporary.replace(archive_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    print("Original ZIP restored and verified: " + str(archive_path), flush=True)
    if args.extract:
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                path = out / name
                if not name.startswith("P_Project/") or out not in path.resolve().parents:
                    raise SystemExit("Unexpected archive path: " + name)
            archive.extractall(out)
        print("Open this project folder in local Codex: " + str(project), flush=True)
        print("Read README.md and docs/local_transfer_guide.md to continue.", flush=True)


if __name__ == "__main__":
    main()
