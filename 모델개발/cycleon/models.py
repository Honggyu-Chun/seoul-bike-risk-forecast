"""Load only manifest-hashed native model files, once at service startup."""
from pathlib import Path
import json

import numpy as np

from .common import digest
from .features import FEATURE_COLUMNS


class ModelBundle:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("purpose") != "cycleon_three_horizon" or self.manifest.get("leakage_diagnostics", False):
            raise ValueError("Ineligible model bundle")
        self.routes = []
        for route in self.manifest["routes"]:
            if not route.get("validated"):
                continue
            if route["columns"] != FEATURE_COLUMNS[route["group"]]:
                raise ValueError("Feature schema mismatch")
            loaded = []
            for spec in route["members"]:
                path = (directory / spec["file"]).resolve()
                if not path.is_relative_to(self.directory) or digest(path) != spec["sha256"]:
                    raise ValueError("Model path or digest mismatch")
                if spec["kind"] == "lightgbm":
                    import lightgbm as lgb
                    model = lgb.Booster(model_file=str(path))
                elif spec["kind"] == "catboost":
                    from catboost import CatBoostClassifier
                    model = CatBoostClassifier(); model.load_model(str(path))
                else:
                    raise ValueError("Unknown model kind")
                loaded.append((spec, model))
            if not loaded:
                raise ValueError("Empty route")
            self.routes.append((route, loaded))

    def predict(self, route_index: int, matrix: np.ndarray, horizon: int):
        route, models = self.routes[route_index]
        members = []
        for spec, model in models:
            if spec.get("horizon") not in (None, horizon):
                continue
            if spec["kind"] == "lightgbm":
                p = model.predict(matrix, num_threads=4)
            else:
                from catboost import Pool
                # All categorical columns are finite integer codes in the kernel.
                cats = [route["columns"].index(c) for c in route.get("categorical_columns", [])]
                if cats:
                    import pandas as pd
                    frame = pd.DataFrame(matrix, columns=route["columns"])
                    for i in cats:
                        frame.iloc[:, i] = frame.iloc[:, i].fillna(0).astype(int)
                    frame = frame.astype({route["columns"][i]: "int64" for i in cats})
                    p = model.predict_proba(Pool(frame, cat_features=cats), thread_count=4)[:, 1]
                else:
                    p = model.predict_proba(matrix, thread_count=4)[:, 1]
            members.append(np.asarray(p))
        if not members:
            raise ValueError("Horizon unavailable in model route")
        p = np.mean(members, axis=0)
        calibration = route["calibration"][str(horizon)]
        logodds = np.log(np.clip(p, 1e-7, 1 - 1e-7) / np.clip(1 - p, 1e-7, 1))
        z = np.clip(calibration["coefficient"] * logodds + calibration["intercept"], -50, 50)
        return 1 / (1 + np.exp(-z))
