"""Read immutable, locally issued hourly model predictions for the HTTP service."""
import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PRIMARY = ("older_weekly_complete3_h1", "profile51_h2", "targetlag48_h3")
LABELS = ("P53", "P51", "P48")
BUNDLE_SHA = "d8d0578241a4bbe7bf6d88c0f450fbfe4c5add985b7886e08a05e265a9121009"


class WeatherPredictions:
    """A receipt is trusted only within its own forecast hour and release."""
    def __init__(self, directory, routes):
        self.root = Path(directory)
        self.routes = routes
        self.base = None
        self.rows = {}
        self.error = None

    def load(self, base, now):
        if base == self.base:
            return self.rows
        self.rows = {}
        self.error = None
        folder = self.root / "slots" / base.astimezone(timezone.utc).strftime("%Y%m%dT%HZ")
        try:
            receipt = json.loads((folder / "issue.json").read_bytes())
            issued = datetime.fromisoformat(receipt["issued_at"])
            if (datetime.fromisoformat(receipt["base"]) != base or
                    not base <= issued <= min(now, base + timedelta(minutes=5)) or
                    receipt["bundle"] != BUNDLE_SHA):
                raise ValueError("weather_issue_mismatch")
            raw = (folder / "paired_predictions.csv").read_bytes()
            if hashlib.sha256(raw).hexdigest() != receipt["predictions_sha256"]:
                raise ValueError("weather_predictions_digest_mismatch")
            counts = Counter()
            rows = {}
            for h, model in enumerate(PRIMARY, 1):
                inputs = receipt["inputs"][str(h)]
                if (inputs["primary"] != self.routes[model]["model_sha256"] or
                        set(inputs["weather"]) != {"T1H","RN1","REH","WSD","PTY"}):
                    raise ValueError("weather_route_mismatch")
            for row in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
                h = int(row["horizon"])
                if h not in (1, 2, 3):
                    raise ValueError("weather_horizon_mismatch")
                model = PRIMARY[h-1]
                risk, rain = float(row["p_primary"]), float(row["forecast_hourly_precip_raw"])
                decision = int(row["decision_primary"])
                key = row["station_id"], h
                if (key in rows or row["model"] != model or not 0 <= risk <= 1 or
                        not math.isfinite(rain) or rain < 0 or
                        decision != int(risk >= self.routes[model]["threshold"]) or
                        datetime.fromisoformat(row["base_utc"]) != base or
                        datetime.fromisoformat(row["target_utc"]) != base + timedelta(hours=h)):
                    raise ValueError("weather_row_mismatch")
                counts[h] += 1
                rows[key] = (row["identity"], int(row["inventory_at_base"]), rain,
                             risk, bool(decision), model, LABELS[h-1])
            if any(counts[h] != receipt["paired_counts"][str(h)] or counts[h] == 0 for h in (1, 2, 3)):
                raise ValueError("weather_station_count_mismatch")
            self.rows, self.base = rows, base
        except FileNotFoundError:
            self.error = "hourly_weather_predictions_pending"
        except (OSError, ValueError, KeyError, TypeError, csv.Error):
            self.error = "hourly_weather_predictions_invalid"
        return self.rows
