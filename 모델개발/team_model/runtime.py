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
        import lightgbm as lgb
        self.directory = install_bundle() if bundle_dir is None else Path(bundle_dir).resolve()
        descriptor = json.loads((RELEASE / 'release.json').read_text(encoding='utf-8'))
        if sha(self.directory / 'manifest.json') != descriptor['members']['manifest.json']:
            raise ValueError('Runtime manifest is not the released manifest')
        self.manifest = json.loads((self.directory / 'manifest.json').read_text(encoding='utf-8'))
        self.allowed_codes = set(self.manifest['trained_station_codes'])
        self.routes = {r['id']: r for r in self.manifest['routes']}
        self.models = {}
        # C is the only enabled live integration route. Advanced routes stay offline.
        for h in (1, 2, 3):
            r = self.routes[f'C_h{h}']
            file = (self.directory / r['model_file']).resolve()
            if not file.is_relative_to(self.directory) or sha(file) != r['model_sha256']:
                raise ValueError('Saved C model digest mismatch')
            model = lgb.Booster(model_file=str(file))
            if model.feature_name() != r['columns'] or model.num_feature() != 8:
                raise ValueError('Saved C feature order mismatch')
            self.models[h] = model
    def predict_c(self, matrix, horizon):
        r = self.routes[f'C_h{horizon}']
        raw = np.asarray(self.models[horizon].predict(np.asarray(matrix, dtype=np.float32), num_threads=1), dtype=np.float32)
        q = np.clip(raw, 1e-7, 1-1e-7)
        params = r['calibration']
        z = np.clip(params['coefficient'] * np.log(q / (1-q)) + params['intercept'], -50, 50)
        risk = np.asarray(1 / (1 + np.exp(-z)), dtype=np.float64)
        if not np.isfinite(risk).all() or ((risk < 0) | (risk > 1)).any():
            raise ValueError('Invalid saved model probability')
        return risk, np.float64(r['threshold'])
