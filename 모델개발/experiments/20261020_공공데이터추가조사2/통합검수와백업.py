"""Validate the saved research catalog and create a local, credential-free snapshot."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import csv
import hashlib
import json
import zipfile
from urllib.parse import urlsplit, parse_qsl

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
FIELDS = ('candidate_id name domain provider official_url service_id temporal_resolution '
          'spatial_resolution historical_coverage live_access publication_or_update_lag '
          'timestamps_available join_method effect_hypothesis evidence_level key_or_approval '
          'cost_or_license admissibility priority next_action verified_at').split()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    with (ROOT / '통합후보목록.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    missing = [(r.get('candidate_id'), k) for r in rows for k in FIELDS if not r.get(k, '').strip()]
    if missing:
        raise ValueError(f'Empty required research fields: {missing}')
    assert len({r['candidate_id'] for r in rows}) == len(rows)
    for row in rows:
        parts = urlsplit(row['official_url'])
        assert parts.scheme in ('http', 'https') and parts.netloc
        assert not any(k.lower() in ('servicekey', 'authkey', 'api_key', 'apikey', 'consumer_secret')
                       for k, _ in parse_qsl(parts.query)), row['candidate_id']
    generated = json.loads((ROOT / '통합검수.json').read_text(encoding='utf-8'))
    assert generated['candidate_rows'] == len(rows)
    for item in generated['inputs']:
        assert sha(ROOT / item['path']) == item['sha256']
    independent = json.loads((ROOT / '부모검수' / 'bus_static_independent_check.json').read_text(encoding='utf-8'))
    assert independent['passed'] is True
    files = sorted(p for p in ROOT.rglob('*') if p.is_file()
                   and '백업' not in p.relative_to(ROOT).parts
                   and '__pycache__' not in p.relative_to(ROOT).parts
                   and p.suffix not in ('.log', '.pid'))
    secrets = []
    env_path = PROJECT / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            value = value.strip().strip('"\'')
            if any(word in key.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and len(value) >= 12:
                secrets.append(value.encode())
    hits = [str(p.relative_to(ROOT)) for p in files
            if any(secret in p.read_bytes() for secret in secrets)]
    if hits:
        raise ValueError(f'Credential scan failed in files: {hits}')
    assert not any(p.name.startswith('.env') for p in files)
    audit = {
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'candidate_rows': len(rows),
        'domain_rows': dict(Counter(r['research_domain'] for r in rows)),
        'common_fields_per_domain': len(FIELDS),
        'empty_required_fields': missing,
        'compiler_input_hashes_match': True,
        'bus_independent_check_passed': True,
        'private_credential_matches': len(hits),
        'new_model_training_for_this_research': False,
        'performance_improvement_proven': False,
        'files_checked': len(files),
    }
    audit_path = ROOT / '최종통합검수.json'
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    files = sorted(set(files + [audit_path]))
    backup_dir = ROOT / '백업'
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%SZ')
    archive = backup_dir / f'공공데이터_추가조사_{stamp}.zip'
    members = []
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            z.write(path, rel)
            members.append({'path': rel, 'bytes': path.stat().st_size, 'sha256': sha(path)})
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for item in members:
            assert hashlib.sha256(z.read(item['path'])).hexdigest() == item['sha256']
    result = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'archive': str(archive), 'archive_bytes': archive.stat().st_size,
              'archive_sha256': sha(archive), 'zip_crc_passed': True,
              'all_member_sha256_match': True, 'members': members,
              'scope': 'Public-data research evidence and report only; training/data artifacts remain in the v4 project.'}
    (backup_dir / '최신백업.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**audit, 'archive_bytes': result['archive_bytes'], 'backup_members': len(members),
                      'zip_crc_passed': True, 'all_member_sha256_match': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
