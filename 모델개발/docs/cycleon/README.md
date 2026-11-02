# CycleOn 모델 API 연동 가이드

2026-11-02 갱신 · AI API v1. 백엔드·프론트·QA가 모델 파일 없이 같은 계약으로 개발하기 위한 진입점이다.

## 바로 시작하기

| 할 일 | 문서 |
| --- | --- |
| 설치하고 목업 호출 | [빠른 시작](QUICKSTART.md) |
| 경로·필드·상태·오류 | [AI API 레퍼런스](API_REFERENCE.md), [AI OpenAPI](../cycleon_ai_openapi.json) |
| 백엔드 입력과 저장·기상 제품 | [백엔드 인수](BACKEND_HANDOFF.md), [데이터 OpenAPI](../cycleon_backend_openapi.json) |
| 화면과 예외 처리 | [프론트 인수](FRONTEND_HANDOFF.md) |
| 파트 간 반드시 맞출 규칙 | [연동 계약](INTEGRATION_CONTRACT.md) |
| 실제 모델·서버 인수 | [실모델 인수](REAL_MODEL_HANDOFF.md) |
| 학습·성능·미완료 상태 | [모델 상태](MODEL_STATUS.md) |
| 호출 자료 | [Postman](CycleOn.postman_collection.json), [합성 JSON](mock_examples.json), [Node 서버 예제](../../examples/cycleon_backend_client.mjs) |

## 제공 범위

Git에는 API·현재 서빙 참조 코드·명세·합성 목업·설치 도구·계약 검사가 있다. 실모델 가중치·전체 연구 데이터·실행 DB·키는 포함하지 않는다. 목업은 고정 시각과 합성 확률이며 모델 성능을 나타내지 않는다.

현재 개발용 실모델은 T/C 세 간격 번들이다. S/C는 후보 제안이며 최종 모델과 추천 정책은 미확정이다. 완료 기반 22개 설정과 후속 연구를 구분하며 64개는 필수 완료 수가 아니다. W/F/E의 기상 출처·공유 특징 검수는 통과했지만 팀 데이터 공급과 실제 서비스 검증은 별도다.

## 연결 구조

프론트 → 앱 백엔드 → AI API. 앱 백엔드는 별도 대여소 목록과 사용자 인증을 제공한다. AI는 내부 데이터 API에서 초기 이력과 변경분을 받아 공통 특징을 만든다. 브라우저에는 AI 서비스 토큰이나 공공 API 키를 넣지 않는다.

AI 경로는 `/v1/status`, `/v1/predictions`, `/v1/recommendations`다. 내부 데이터는 `/internal/ai-data/v1/stations`, `/observations`, `/forecasts`다. 프론트 공개 앱 경로·사용자 인증·갱신 주기는 팀 합의 대상이며 이 저장소가 앱 전체 백엔드를 구현한 것은 아니다.

## 시작 확인

- 설치 뒤 계약 검사 33개가 통과한다.
- 실제 로컬 HTTP에서 상태·세 간격·unknown ID의 null·추천 보류를 확인한다.
- `recommendation-demo`와 `not-ready`도 실행해 화면 상태를 구현한다.
- 실제 모델 전환 시에는 접속/번들·해시·데이터 이력·보정·정책·토큰을 별도 인수한다.

상세 개발은 [협업 규칙](../../../CONTRIBUTING.md)을 따른다. API 타입·의미를 변경하면 코드·OpenAPI·예시·클라이언트를 함께 갱신한다.
