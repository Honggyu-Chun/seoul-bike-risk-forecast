# CycleOn 협업 개발

팀별 프레임워크·코드 구성은 각자 선택한다. 서로 연결되는 요청·응답·시간·대여소 ID·오류 의미는 같은 계약을 사용한다.

## 시작과 브랜치

`main`을 받은 뒤 `feature/backend-ai`, `feature/frontend-map`, `fix/prediction-null`처럼 실제 작업 이름으로 브랜치를 만든다. 이미 작업한 저장소는 미커밋 변경을 보존한 뒤 fetch/pull한다. 기존 파일을 reset/clean으로 덮어쓰지 않는다.

신규 팀원은 [빠른 시작](모델개발/docs/cycleon/QUICKSTART.md)으로 목업을 실행한다. 실제 모델·자료·API 키는 화면과 중계 개발의 선행 조건이 아니다.

## 계약을 바꿀 때

1. [파트 간 계약](모델개발/docs/cycleon/INTEGRATION_CONTRACT.md)과 AI/데이터 OpenAPI를 먼저 확인한다.
2. 요청·응답 타입이나 의미를 바꾸면 `contracts.py`·`api.py`, mock과 예시를 함께 갱신한다.
3. `모델개발`에서 `.venv-contract`의 Python으로 `scripts/cycleon_contract_check.py --export`를 실행하고 생성 diff를 확인한다. 검사는 `--export` 없이 다시 실행한다.
4. 영향을 받는 프론트·백엔드의 호출 방식과 호환성을 PR에 설명한다. 단순 모델 ID·가중치 교체와 API 파괴 변경을 구분한다.

개별 파트는 필요한 검증과 병합을 진행한다. CI는 API 계약만 확인하며 실제 프론트·DB·운영 성공을 대신하지 않는다. 의미가 바뀌는 변경은 연결되는 파트와 먼저 합의한다.

## 로컬 확인

```powershell
cd 모델개발
.\.venv-contract\Scripts\python.exe scripts/cycleon_contract_check.py
# 다른 터미널에서 목업을 실행한 후
$env:CYCLEON_MOCK_TOKEN = 'local-mock-only-change-this-token-123456'
.\.venv-contract\Scripts\python.exe scripts/cycleon_http_check.py
```

공유 대상은 코드·명세·합성 예시·검증 결과 요약이다. 실제 키·토큰·세션·`.env`·가중치·원본 데이터·실행 DB·개인별 런타임은 커밋하지 않는다. 실제 모델 연결 인수는 [별도 절차](모델개발/docs/cycleon/REAL_MODEL_HANDOFF.md)를 따른다.
