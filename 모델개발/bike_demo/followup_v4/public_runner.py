"""Isolated public-data ablations using the frozen v4 learner and time windows."""
from contextlib import contextmanager
import argparse
import copy
import gc
import json
import os
import time
import traceback
import joblib
import numpy as np
import pandas as pd
from scipy.special import logit
from .common import ROOT, OUT as BASE_OUT, RECORD as BASE_RECORD, save, digest, now
from .fast_runner import Inputs as BaseInputs
from .public_features import Store, BUS_COLUMNS, OD_COLUMNS, DEST as FEATURE_DIR
from .checkpoint import worker_pid_alive
from . import runner

OUT = ROOT / 'artifacts/followup_public_20261020'
RECORD = ROOT / 'experiments/20261020_추가공공데이터적용'
PLAN = RECORD / '후속비교_계획.json'
GROUPS = {'bus': BUS_COLUMNS, 'od': OD_COLUMNS, 'bus_od': BUS_COLUMNS + OD_COLUMNS}


def plan():
    value = json.loads(PLAN.read_text(encoding='utf-8'))
    assert digest(BASE_RECORD / 'protocol.json') == value['v4_protocol_sha256']
    assert digest(FEATURE_DIR / 'metadata.json') == value['feature_metadata_sha256']
    assert digest(RECORD / '특징정의와근거.md') == value['definition_document_sha256']
    assert digest(runner.__file__) == value['inherited_fit_function_source_sha256']
    from . import fast_frame
    assert digest(fast_frame.__file__) == value['inherited_reader_source_sha256']
    if value.get('public_runner_source_sha256'):
        assert digest(__file__) == value['public_runner_source_sha256'], 'Public fitting code changed after freeze'
    assert value['registered_comparisons'] == len(value['folds']) * len(value['candidate_specs']) == 9
    assert value['historical_reception_verified'] is False
    assert value['information_track'] == 'public_archive_and_current_crosswalk_sensitivity'
    return value


class Inputs:
    def __init__(self):
        # Construct this before rebinding runner's output globals. All original
        # quantities and features are read from v4; only new results go elsewhere.
        self.base = BaseInputs()
        self.store = Store()
        for name in ('q', 'n', 'times', 'tt', 'target', 'known'):
            setattr(self, name, getattr(self.base, name))
        with np.load(BASE_OUT / 'data/panel_2025.npz') as panel:
            ids = panel['station_ids'].tolist()
        assert self.store.meta['station_ids'] == ids
        self.meta = copy.deepcopy(self.base.meta)
        base = self.meta['groups']['hazard_spatial']
        assert len(base) == 105 and not set(base).intersection(BUS_COLUMNS + OD_COLUMNS)
        for group, extra in GROUPS.items():
            self.meta['groups'][group] = base + extra

    def indices(self, start, end):
        return self.base.indices(start, end)

    def frame(self, index, columns):
        extra = [c for c in columns if c in BUS_COLUMNS + OD_COLUMNS]
        original = [c for c in columns if c not in extra]
        if any(c not in self.base.columns for c in original):
            raise ValueError('Unknown base feature')
        frame = self.base.frame(index, original)
        extension = self.store.frame(index, self.times, self.n, extra)
        for column in extra:
            frame[column] = extension[column].to_numpy(copy=False)
        return frame if list(frame.columns) == columns else frame.loc[:, columns]


@contextmanager
def isolated_output(configuration, destination=OUT, record=RECORD):
    previous = runner.OUT, runner.RECORD, runner.protocol
    try:
        runner.OUT, runner.RECORD = destination, record
        runner.protocol = lambda: configuration
        yield
    finally:
        runner.OUT, runner.RECORD, runner.protocol = previous


def v4_wait_reasons():
    reasons = []
    launch = BASE_OUT / 'queue_launch.json'
    if launch.exists():
        pid = json.loads(launch.read_text(encoding='utf-8')).get('launcher_pid')
        if pid and worker_pid_alive(pid):
            reasons.append('v4 queue launcher is live')
    worker = BASE_OUT / 'worker.json'
    if worker.exists():
        state = json.loads(worker.read_text(encoding='utf-8'))
        if state.get('phase') == 'fit' and worker_pid_alive(state.get('pid', 0)):
            reasons.append('v4 model worker is live')
    review_path = BASE_RECORD / 'completion_review.json'
    if not review_path.exists():
        reasons.append('v4 result review is absent')
    else:
        review = json.loads(review_path.read_text(encoding='utf-8'))
        if review['missing_comparisons'] or not review['confirmation_selection_exists']:
            reasons.append('registered v4 comparisons or confirmation remain incomplete')
        if review['artifact_issues'] or review['protected_files_changed']:
            reasons.append('v4 artifact or protected-source validation failed')
    return reasons


@contextmanager
def training_lock():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'training_lock.json'
    if path.exists():
        old = json.loads(path.read_text(encoding='utf-8'))
        if worker_pid_alive(old['pid']):
            raise RuntimeError('Another public-data comparison process is live')
        path.replace(OUT / ('stale_training_lock_' + str(time.time_ns()) + '.json'))
    with path.open('x', encoding='utf-8') as f:
        json.dump(dict(pid=os.getpid(), created_at_utc=now()), f)
    try:
        yield
    finally:
        if path.exists() and json.loads(path.read_text(encoding='utf-8'))['pid'] == os.getpid():
            path.unlink()


def verify_one(data, fold, spec, configuration):
    import lightgbm as lgb
    path = OUT / fold / (spec['name'] + '.json')
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['spec'] == spec and record['protocol_sha256'] == digest(RECORD / 'protocol.json')
    assert record['data_sha256'] == data.meta['panel_sha256']
    record.update(status='trained_pending_checks', information_track=configuration['information_track'],
                  public_extension_plan_sha256=digest(PLAN), public_feature_metadata_sha256=digest(FEATURE_DIR / 'metadata.json'),
                  public_runner_sha256=digest(__file__), historical_reception_verified=False,
                  historical_station_ID_identity_verified=False, independent_final_test=False,
                  interpretation='conditional prior archive and current crosswalk comparison; not proven historical live reception')
    save(path, record)
    model_dir = OUT / fold / 'models'
    model = lgb.Booster(model_str=(model_dir / (spec['name'] + '.txt')).read_text(encoding='utf-8'))
    calibrator = joblib.load(model_dir / (spec['name'] + '_calibrator.joblib'))
    split = configuration['development_folds'][fold]
    checks = []
    for stage in ('cal', 'policy', 'valid'):
        index = data.indices(split[stage + '_start'], split[stage + '_end'])
        frame = data.frame(index, record['features'])
        exact = model.predict(frame, num_threads=4)
        del frame
        stored = np.load(OUT / 'predictions' / fold / (spec['name'] + '_' + stage + '.npy'), mmap_mode='r')
        assert exact.shape == stored.shape and np.isfinite(exact).all()
        error = float(np.max(np.abs(exact - stored)))
        assert error < 2e-6, 'Reloaded model and saved raw predictions disagree'
        # Preserve double precision separately. The inherited runner saves float32
        # raw arrays; these exact copies support subsequent decision reconstruction.
        np.save(OUT / 'predictions' / fold / (spec['name'] + '_' + stage + '_exact.npy'), exact)
        calibrated = calibrator.predict_proba(logit(np.clip(exact, 1e-6, 1-1e-6)).reshape(-1, 1))[:, 1].astype(np.float32)
        if stage == 'valid':
            value = runner.summarize((data.target[index] == 0).astype(np.uint8), calibrated,
                                     data.q.ravel()[index], record['rule'])
            for group in ('overall', 'current_positive', 'current_1_to_3'):
                for field in ('n', 'zero_cases', 'tn', 'fp', 'fn', 'tp'):
                    assert value[group][field] == record['validation'][group][field], (group, field)
                for field in ('f1', 'precision', 'recall', 'average_precision', 'brier'):
                    assert abs(value[group][field] - record['validation'][group][field]) < 1e-8, (group, field)
        checks.append(dict(stage=stage, rows=len(index), raw_maximum_absolute_error=error))
        del exact, stored, calibrated
        gc.collect()
    artifact_paths = [model_dir / (spec['name'] + '.txt'), model_dir / (spec['name'] + '_calibrator.joblib')]
    artifact_paths += [OUT / 'predictions' / fold / (spec['name'] + '_' + stage + suffix + '.npy')
                      for stage in ('cal','policy','valid') for suffix in ('', '_exact')]
    artifacts = [dict(path=str(p.relative_to(OUT)), bytes=p.stat().st_size, sha256=digest(p)) for p in artifact_paths]
    record['public_checks'] = dict(all_passed=True, all_stage_raw_predictions_checked=True,
                                   entire_validation_counts_and_metrics_match=True, checks=checks,
                                   artifact_hashes=artifacts,
                                   verified_at_utc=now())
    record['status'] = 'ok'
    save(path, record)
    print('PUBLIC_MODEL_VERIFIED', fold, spec['name'], flush=True)


def preflight(configuration):
    data = Inputs()
    index = np.array([0, data.n - 1, data.n, 24 * data.n - 1, 1000 * data.n, 0])
    base_columns = data.meta['groups']['hazard_spatial']
    reference = data.base.frame(index, base_columns)
    checks = []
    for group, extra in GROUPS.items():
        frame = data.frame(index, base_columns + extra)
        np.testing.assert_allclose(frame[base_columns], reference, equal_nan=True, rtol=0, atol=0)
        checks.append(dict(group=group, features=len(frame.columns), base_features_unchanged=True,
                           repeated_index_order_preserved=bool(frame.iloc[0].equals(frame.iloc[-1]))))
        assert checks[-1]['repeated_index_order_preserved']
    value = dict(checked_at_utc=now(), public_extension_plan_sha256=digest(PLAN), source_hashes_valid=True,
                 cohort_size=data.n, join_checks=checks, v4_wait_reasons=v4_wait_reasons(),
                 main_training_started=False, held_back_Q3_Q4_read=False)
    save(RECORD / '후속비교_실행전검수.json', value)
    print('PUBLIC_PREFLIGHT', json.dumps(value, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--folds', nargs='+')
    parser.add_argument('--names', nargs='+')
    args = parser.parse_args()
    configuration = plan()
    if args.preflight:
        preflight(configuration)
        return
    reasons = v4_wait_reasons()
    if reasons:
        raise RuntimeError('Public-data model fitting deferred: ' + '; '.join(reasons))
    folds = args.folds or configuration['folds']
    assert set(folds) <= set(configuration['folds'])
    selected = [s for s in configuration['candidate_specs'] if not args.names or s['name'] in args.names]
    assert selected and (not args.names or set(args.names) == {s['name'] for s in selected})
    protocol_path = RECORD / 'protocol.json'
    if protocol_path.exists():
        assert json.loads(protocol_path.read_text(encoding='utf-8')) == configuration
    else:
        save(protocol_path, configuration)
    with training_lock():
        data = Inputs()
        with isolated_output(configuration):
            for fold in folds:
                for spec in selected:
                    path = OUT / fold / (spec['name'] + '.json')
                    try:
                        existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
                        if existing is not None:
                            assert existing['protocol_sha256'] == digest(protocol_path)
                            assert existing['spec'] == spec
                            if existing.get('public_extension_plan_sha256'):
                                assert existing['public_extension_plan_sha256'] == digest(PLAN)
                            if existing.get('public_runner_sha256'):
                                assert existing['public_runner_sha256'] == digest(__file__)
                        if (existing and existing['status'] == 'ok' and existing.get('public_checks', {}).get('all_passed')):
                            print('SKIP_PUBLIC_VERIFIED', fold, spec['name'], flush=True)
                            continue
                        if not existing or existing['status'] not in ('ok', 'trained_pending_checks'):
                            print('START_PUBLIC', fold, spec['name'], now(), flush=True)
                            runner.run_one(data, fold, spec)
                        verify_one(data, fold, spec, configuration)
                        runner.refresh()
                    except Exception as exc:
                        save(OUT / fold / (spec['name'] + '_error.json'), dict(status='error', message=str(exc), traceback=traceback.format_exc(), spec=spec))
                        raise
            save(OUT / 'worker.json', dict(pid=os.getpid(), phase='completed', updated_at_utc=now()))
    print('PUBLIC_COMPARISON_QUEUE_COMPLETE', flush=True)


if __name__ == '__main__':
    main()
