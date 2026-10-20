"""Fetch only the observed official 2024-11 aggregate archive; no labels or secrets."""
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parent
CAP = 200_000_000


def main():
    catalog = ROOT / 'OA-21229_수신시점_catalog.html'
    raw_catalog = catalog.read_bytes()
    text = raw_catalog.decode('utf-8')
    rows = [x for x in re.findall(r'<tr\b.*?</tr>', text, re.S | re.I)
            if 'tpss_bcycl_od_statnhm_202411.zip' in x]
    assert len(rows) == 1
    seq = re.search(r"downloadFile\('([^']+)'\)", rows[0]).group(1)
    assert seq == '2411'
    assert '2024.12.08.' in rows[0]
    target = ROOT / '원본' / 'tpss_bcycl_od_statnhm_202411.zip'
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        raise FileExistsError('Archive already exists; inspect and reuse the recorded receipt.')
    url = 'https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false'
    payload = {'infId': 'OA-21229', 'seqNo': '', 'seq': seq, 'infSeq': '1'}
    request = Request(url, data=urlencode(payload).encode(),
                      headers={'User-Agent': 'bike-research-source-audit/1.0',
                               'Referer': 'https://data.seoul.go.kr/dataList/OA-21229/F/1/datasetView.do'})
    started = datetime.now(timezone.utc).isoformat()
    partial = target.with_suffix('.zip.part')
    size = 0
    checksum = hashlib.sha256()
    with urlopen(request, timeout=60) as response, partial.open('xb') as output:
        assert response.status == 200
        expected = response.headers.get('Content-Length')
        if expected and int(expected) > CAP:
            raise ValueError('Archive exceeds declared intake cap')
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > CAP:
                raise ValueError('Archive exceeds declared intake cap')
            checksum.update(block)
            output.write(block)
        response_meta = {'status': response.status, 'content_type': response.headers.get('Content-Type'),
                         'content_length': expected, 'last_modified': response.headers.get('Last-Modified')}
    assert zipfile.is_zipfile(partial), 'Received body is not a ZIP archive; stop without retry'
    partial.replace(target)
    with zipfile.ZipFile(target) as archive:
        members = [{'name': x.filename, 'bytes': x.file_size, 'compressed_bytes': x.compress_size,
                    'crc32': f'{x.CRC:08x}'} for x in archive.infolist()]
        assert sum(x['bytes'] for x in members) < 3_000_000_000
    receipt = {
        'started_at_utc': started, 'received_at_utc': datetime.now(timezone.utc).isoformat(),
        'catalog_url': 'https://data.seoul.go.kr/dataList/OA-21229/F/1/datasetView.do',
        'catalog_sha256': hashlib.sha256(raw_catalog).hexdigest(), 'download_url': url,
        'post_parameters': payload, 'response': response_meta,
        'archive': str(target.relative_to(ROOT)), 'bytes': size, 'sha256': checksum.hexdigest(),
        'plan_sha256_before_fetch': hashlib.sha256((ROOT / '준비계획.md').read_bytes()).hexdigest(),
        'catalog_modified_date': '2024-12-08', 'first_publication_proven': False,
        'historical_received_at_proven': False, 'historical_original_unchanged_proven': False,
        'target_data_month': '2024-11', 'private_key_used': False, 'members': members,
        'scope': 'Prior-period aggregate source inspection; no new model scores or held-back labels.'}
    (ROOT / '202411_OD_수신기록.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PRIOR_OD_ARCHIVE_RECEIVED', size, 'bytes', len(members), 'members', flush=True)
    print(json.dumps(members[:5], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
