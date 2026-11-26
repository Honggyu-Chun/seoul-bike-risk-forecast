"""Saved model inference only. Does not train, collect, or attest provider data."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import zipfile
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'releases/team-model-20261110'

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def install_bundle(target=None):
    descriptor = json.loads((RELEASE / 'release.json').read_text(encoding='utf-8'))
    archive = RELEASE / descriptor['archive']
    if sha(archive) != descriptor['sha256']:
        raise ValueError('Model archive SHA-256 mismatch')
    target = Path(target) if target else ROOT / 'artifacts/team_model' / descriptor['sha256'][:16]
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > 150_000_000:
            raise ValueError('Unexpected expanded model archive size')
        for member in z.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target) or member.is_dir():
                raise ValueError('Invalid model archive member')
            content = z.read(member)
            if member.filename not in descriptor['members'] or hashlib.sha256(content).hexdigest() != descriptor['members'][member.filename]:
                raise ValueError('Model archive member SHA-256 mismatch')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if sha(destination) != descriptor['members'][member.filename]:
                    raise ValueError('Installed immutable bundle differs; use a new directory')
            else:
                with destination.open('xb') as out:
                    out.write(content)
    return target

class Runtime:
    def __init__(self, bundle_dir=None):
        self.directory = install_bundle() if bundle_dir is None else Path(bundle_dir).resolve()
        descriptor = json.loads((RELEASE / 'release.json').read_text(encoding='utf-8'))
        if sha(self.directory / 'manifest.json') != descriptor['members']['manifest.json']:
            raise ValueError('Runtime manifest is not the released manifest')
        self.manifest = json.loads((self.directory / 'manifest.json').read_text(encoding='utf-8'))
        self.allowed_codes = set(self.manifest['trained_station_codes'])
        self.routes = {r['id']: r for r in self.manifest['routes']}
        self.models = {}
        for h in (1, 2, 3):
            self._model(f'C_h{h}')

    def _model(self, model_id):
        if model_id not in self.models:
            import lightgbm as lgb
            route = self.routes[model_id]
            file = (self.directory / route['model_file']).resolve()
            if not file.is_relative_to(self.directory) or sha(file) != route['model_sha256']:
                raise ValueError('Saved model digest mismatch')
            model = lgb.Booster(model_file=str(file))
            if model.feature_name() != route['columns']:
                raise ValueError('Saved model feature order mismatch')
            self.models[model_id] = model
        return self.models[model_id]

    def predict_route(self, model_id, matrix):
        route = self.routes[model_id]
        features = np.asarray(matrix, dtype=np.float32)
        if features.ndim != 2 or features.shape[1] != len(route['columns']):
            raise ValueError('Incorrect feature matrix dimensions')
        raw = np.asarray(self._model(model_id).predict(features, num_threads=1), dtype=np.float32)
        q = np.clip(raw, 1e-7, 1-1e-7)
        params = route['calibration']
        z = np.clip(params['coefficient'] * np.log(q / (1-q)) + params['intercept'], -50, 50)
        risk = np.asarray(1 / (1 + np.exp(-z)), dtype=np.float64)
        if not np.isfinite(risk).all() or ((risk < 0) | (risk > 1)).any():
            raise ValueError('Invalid saved model probability')
        return risk, route['threshold']

    def predict_c(self, matrix, horizon):
        return self.predict_route(f'C_h{horizon}', matrix)
