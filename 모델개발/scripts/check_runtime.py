"""Check the archived demo locally without calling live public APIs."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from bike_demo.api import app


def main():
    with TestClient(app) as client:
        health = client.get('/api/health')
        if health.status_code != 200 or not health.json()['models_ready']:
            raise SystemExit('준비 파일이 없다. 먼저 python scripts/setup_runtime.py를 실행한다.')
        overview = client.get('/api/overview')
        assert overview.status_code == 200
        at = overview.json()['times'][0]
        snapshot = client.get('/api/snapshot', params={'at':at, 'lat':37.5665, 'lon':126.978, 'radius':500})
        assert snapshot.status_code == 200
        for path in ['/', '/static/app.js', '/api/research', '/api/research/report', '/offline']:
            assert client.get(path).status_code == 200, path
        assert client.get('/api/city', params={'at':at}).status_code == 200
    print('실행 준비 확인 완료: 화면·모델 자료·과거 예측·결과표·보고서·오프라인 화면')


if __name__ == '__main__':main()
