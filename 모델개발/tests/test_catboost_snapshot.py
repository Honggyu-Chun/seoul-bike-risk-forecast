import numpy as np
import pandas as pd
import pytest
from catboost import CatBoostClassifier
from bike_demo.followup_v4 import snapshots


def test_catboost_resumes_in_unicode_workspace_and_rejects_changed_inputs(tmp_path, monkeypatch):
    root = tmp_path / '한글 작업공간'
    models = root / 'artifacts' / 'A' / 'models'
    models.mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(snapshots, 'ROOT', root)
    rng = np.random.default_rng(310)
    x = pd.DataFrame(rng.normal(size=(1000, 5)), columns=list('abcde'))
    y = (x.a + x.b * .3 > .1).to_numpy(dtype=np.uint8)
    tx, sx, ty, sy = x.iloc[:800], x.iloc[800:], y[:800], y[800:]
    spec = dict(name='catboost_check', rounds=45, seed=20261020, depth=3, kind='cat')
    options, info = snapshots.catboost_snapshot(models, spec, tx, ty, sx, sy, interval=1)
    params = dict(iterations=45, depth=3, random_seed=20261020, thread_count=1,
                  learning_rate=.06, verbose=False)

    class Interrupt:
        def after_iteration(self, progress):
            return progress.iteration < 9

    partial = CatBoostClassifier(**params, **options)
    partial.fit(tx, ty, eval_set=(sx, sy), callbacks=[Interrupt()], use_best_model=False)
    assert partial.tree_count_ == 9
    assert (root / info['snapshot_file']).stat().st_size > 0
    options, info = snapshots.catboost_snapshot(models, spec, tx, ty, sx, sy, interval=1)
    assert info['existing_snapshot_before_fit']
    resumed = CatBoostClassifier(**params, **options)
    resumed.fit(tx, ty, eval_set=(sx, sy), use_best_model=False)
    direct = CatBoostClassifier(**params, allow_writing_files=False)
    direct.fit(tx, ty, eval_set=(sx, sy), use_best_model=False)
    assert resumed.tree_count_ == 45
    np.testing.assert_allclose(resumed.predict_proba(sx), direct.predict_proba(sx), atol=1e-12)
    changed = tx.copy()
    changed.iloc[0, 0] += .1
    with pytest.raises(RuntimeError, match='inputs changed'):
        snapshots.catboost_snapshot(models, spec, changed, ty, sx, sy)
