"""Install the isolated model-free collaboration environment and verify contracts."""
from pathlib import Path
import subprocess
import sys
import venv


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Python 3.12 is required. Use py -3.12 or python3.12.")
    root = Path(__file__).resolve().parents[1]
    env = root / ".venv-contract"
    python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if python.exists():
        version = subprocess.check_output([str(python), "-c", "import sys; print('%s.%s' % sys.version_info[:2])"], text=True).strip()
        if version != "3.12":
            raise SystemExit("Existing .venv-contract uses another Python. Preserve it and create a Python 3.12 environment.")
    else:
        venv.EnvBuilder(with_pip=True).create(env)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements-contract.txt")], cwd=root, check=True)
    subprocess.run([str(python), str(root / "scripts/cycleon_contract_check.py")], cwd=root, check=True)
    print("Ready: use .venv-contract Python and scripts/cycleon_mock_service.py ai")


if __name__ == "__main__":
    main()
