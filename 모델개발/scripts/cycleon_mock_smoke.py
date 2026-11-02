"""Run isolated mock HTTP checks on unused loopback ports; always stop owned children."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "local-smoke-only-" + "x" * 32


def start(mode, scenario="policy-pending"):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["CYCLEON_MOCK_TOKEN"] = TOKEN
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/cycleon_mock_service.py"), mode,
         "--port", str(port), "--scenario", scenario], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=.3, trust_env=False) as client:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Owned mock exited before readiness")
                try:
                    if client.get(url + "/openapi.json").status_code == 200:
                        return process, url, env
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
        raise RuntimeError("Owned mock readiness timed out")
    except BaseException:
        stop(process)
        raise


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    backend, backend_url, _ = start("backend")
    results = []
    try:
        for scenario in ("policy-pending", "recommendation-demo", "not-ready"):
            ai, ai_url, env = start("ai", scenario)
            try:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/cycleon_http_check.py"),
                     "--ai-url", ai_url, "--backend-url", backend_url],
                    cwd=ROOT, env=env, check=True, capture_output=True, text=True,
                    timeout=30,
                )
                results.append({"scenario": scenario, **json.loads(result.stdout)})
            finally:
                stop(ai)
    finally:
        stop(backend)
    print(json.dumps({"scenarios": results, "owned_children_stopped": True,
                      "real_model_loaded": False, "real_provider_calls": 0}))


if __name__ == "__main__":
    main()
