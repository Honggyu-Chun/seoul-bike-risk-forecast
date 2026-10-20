"""Snapshot prepared inputs, evidence, figures and code, keeping the raw ZIP in place."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def main():
    receipt = json.loads((ROOT / '202411_OD_수신기록.json').read_text(encoding='utf-8'))
    raw = ROOT / receipt['archive']
    assert sha(raw) == receipt['sha256']
    pairs = [(p, p.relative_to(ROOT).as_posix()) for p in sorted(ROOT.rglob('*'))
             if p.is_file() and not {'백업','원본','__pycache__'}.intersection(p.relative_to(ROOT).parts)
             and p.suffix not in ('.log','.pid')]
    features = PROJECT / 'artifacts/followup_public_features_20261020'
    pairs.extend((p, '입력배열/' + p.name) for p in sorted(features.iterdir()) if p.is_file())
    for path in ('bike_demo/followup_v4/public_features.py','bike_demo/followup_v4/public_runner.py',
                 'bike_demo/followup_v4/public_report.py','tests/test_public_feature_join.py',
                 'tests/test_public_runner_isolation.py'):
        pairs.append((PROJECT / path, '공통코드/' + path))
    completed = []
    public_out = PROJECT / 'artifacts/followup_public_20261020'
    plan_hash = sha(ROOT / '후속비교_계획.json')
    for fold in ('A','B','C'):
        for p in sorted((public_out / fold).glob('lightgbm_public_*.json')):
            r = json.loads(p.read_text(encoding='utf-8'))
            if r.get('status')!='ok' or not r.get('public_checks',{}).get('all_passed'):
                continue
            assert r['public_extension_plan_sha256'] == plan_hash
            pairs.append((p,'학습결과/'+p.relative_to(public_out).as_posix()))
            for item in r['public_checks']['artifact_hashes']:
                artifact = public_out / item['path']
                assert sha(artifact) == item['sha256']
                pairs.append((artifact,'학습결과/'+artifact.relative_to(public_out).as_posix()))
            completed.append(dict(fold=fold, model=r['name'], result_sha256=sha(p)))
    secrets = []
    for line in (PROJECT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            v = v.strip().strip('"\'')
            if any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD')) and len(v) >= 12:
                secrets.append(v.encode())
    assert not any(p.name.startswith('.env') for p, _ in pairs)
    for p, _ in pairs:
        assert not any(v in p.read_bytes() for v in secrets), 'Credential found; stop snapshot'
    backup = ROOT / '백업'
    backup.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%fZ')
    archive = backup / ('추가입력_코드와정제본_' + stamp + '.zip')
    members = []
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p, member in pairs:
            z.write(p, member)
            members.append(dict(path=member, bytes=p.stat().st_size, sha256=sha(p)))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for item in members:
            assert hashlib.sha256(z.read(item['path'])).hexdigest() == item['sha256']
    metadata = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), archive=str(archive),
                    archive_bytes=archive.stat().st_size, archive_sha256=sha(archive),
                    zip_crc_passed=True, all_member_sha256_match=True, credential_matches=0,
                    original_source_retained_in_place=dict(path=str(raw), bytes=raw.stat().st_size,
                                                            sha256=receipt['sha256'], verified=True),
                    scope='Prepared public-data inputs and evidence; full original project and v4 models remain separately saved.',
                    public_data_model_fits_completed=len(completed), registered_next_comparisons=9,
                    completed_results=completed, pending_public_comparisons=9-len(completed), members=members)
    (backup / '최신백업.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PUBLIC_INPUT_CHECKPOINT_SAVED', len(members), 'members', archive.stat().st_size, 'bytes', flush=True)


if __name__ == '__main__':
    main()
