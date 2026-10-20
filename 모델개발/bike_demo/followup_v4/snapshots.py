"""Persist CatBoost progress only when the exact fit inputs still match."""
import hashlib
import json
from pathlib import Path
import numpy as np
from .common import ROOT, save, now


def _update_array(hasher, array):
    value = np.ascontiguousarray(array)
    hasher.update(str(value.dtype).encode('ascii'))
    hasher.update(str(value.shape).encode('ascii'))
    hasher.update(memoryview(value).cast('B'))


def catboost_snapshot(model_dir, spec, train, labels, stop, stop_labels, interval=60):
    import catboost
    if Path.cwd().resolve() != ROOT.resolve():
        raise RuntimeError('Snapshot training must run from the model-development root')
    folder = Path(model_dir).resolve().parent / 'snapshots' / spec['name']
    relative = folder.relative_to(ROOT).as_posix()
    relative.encode('ascii')  # Native Windows writers require this relative ASCII path.
    folder.mkdir(parents=True, exist_ok=True)
    identity = dict(spec=spec, columns=list(train.columns),
                    catboost_version=catboost.__version__, early_stopping_rounds=60,
                    learning_rate=.06, l2_leaf_reg=5, border_count=64,
                    has_time=True, threads=4, loss='Logloss')
    h = hashlib.sha256(json.dumps(identity, sort_keys=True).encode('utf-8'))
    for frame, target in ((train, labels), (stop, stop_labels)):
        for name in frame:
            h.update(name.encode('utf-8'))
            _update_array(h, frame[name].to_numpy())
        _update_array(h, target)
    signature = h.hexdigest()
    registration = folder / 'input_identity.json'
    snapshot = folder / 'state.cbsnapshot'
    if registration.exists():
        previous = json.loads(registration.read_text(encoding='utf-8'))
        if previous['input_sha256'] != signature:
            raise RuntimeError('Snapshot inputs changed; preserve this run and use a new run ID')
    elif snapshot.exists():
        raise RuntimeError('Unregistered snapshot found; refusing to reuse unknown inputs')
    else:
        save(registration, dict(registered_at_utc=now(), input_sha256=signature, **identity))
    options = dict(allow_writing_files=True, train_dir=relative, save_snapshot=True,
                   snapshot_file='state.cbsnapshot', snapshot_interval=interval)
    info = dict(snapshot_file=snapshot.relative_to(ROOT).as_posix(),
                snapshot_interval_seconds=interval, snapshot_input_sha256=signature,
                existing_snapshot_before_fit=snapshot.exists())
    return options, info
