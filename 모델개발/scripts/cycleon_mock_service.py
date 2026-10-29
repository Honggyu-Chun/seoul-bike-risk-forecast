"""Run the model-free contract API on loopback; never starts a collector/trainer."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cycleon.mock_api import SCENARIOS, mock_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("ai", "backend"))
    parser.add_argument("--scenario", choices=SCENARIOS, default="policy-pending")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    token = os.environ.get("CYCLEON_MOCK_TOKEN", "")
    if len(token) < 32:
        parser.error("Set CYCLEON_MOCK_TOKEN to a local test token of at least 32 characters")
    port = args.port if args.port is not None else (18011 if args.mode == "ai" else 18012)
    if port in (8000, 8001, 8002) or not 1024 <= port <= 65535:
        parser.error("Use an unprivileged mock port other than 8000/8001/8002")
    import uvicorn
    print("MOCK: synthetic values, fixed clock, no trained model or live data.")
    uvicorn.run(mock_app(args.mode, token, args.scenario), host="127.0.0.1", port=port, access_log=False)


if __name__ == "__main__":
    main()
