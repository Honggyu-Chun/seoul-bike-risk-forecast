# 백엔드 인수 명세

> Draft v0.2 · 데이터 인계 기준 2026-11-02. SQLite는 참조 구현이며 팀 DB 선택을 제한하지 않는다. 로컬 갱신이며 원격 Git 반영과 구분한다.

## 1. 제공해야 할 경계

팀 백엔드는 수집·저장 계층의 세 내부 API를 구현하거나 참조 서버를 연결한다. AI 서비스는 초기 이력과 변경분을 캐시에 동기화해 공통 특징을 생성한다. 사용자 조회마다 전체 과거 데이터를 다시 읽거나 모델을 재학습하지 않는다.

프론트용 대여소 목록·사용자 인증·AI 중계 API는 애플리케이션 백엔드가 별도로 구현한다. AI가 쓰는 서비스 토큰은 서버 환경변수/비밀 저장소에서 읽는다. 실제 공공 API 키와 서비스 토큰은 서로 다른 자격정보다.

| 방향 | 계약 | 설명 |
| --- | --- | --- |
| AI → 데이터 백엔드 | `GET /internal/ai-data/v1/stations` | 대여소 ID·좌표·이력 |
| AI → 데이터 백엔드 | `GET /internal/ai-data/v1/observations` | 재고·기상 실황 등 수신 이력 |
| AI → 데이터 백엔드 | `GET /internal/ai-data/v1/forecasts` | 발표본·목표 시각별 예보 |
| 앱 백엔드 → AI | `/v1/status`, `/v1/predictions`, `/v1/recommendations` | [AI 계약](API_REFERENCE.md) |

## 2. 페이지 계약

세 경로 모두 Bearer 인증, 200의 body는 Page다. [데이터 OpenAPI](../cycleon_backend_openapi.json)가 타입의 기준이다.

| 쿼리 | 범위·기본 | 적용 |
| --- | --- | --- |
| `cursor` | 정수 ≥0, 기본 0 | 세 API |
| `limit` | 정수 1~10000, 기본 1000 | 세 API |
| `watermark` | 정수 ≥0, 처음 생략 | 세 API |
| `source` | 문자열, 생략 가능 | observations/forecasts |
| `start`, `end` | timezone 있는 ISO 8601, 생략 가능 | observations/forecasts의 **received_at** 필터, 양 끝 포함 |

```json
{
  "items":[{"cursor":1,"conflict":false,"capture_id":null,"record":{"...":"Station / Observation / Forecast"}}],
  "next_cursor":1,"watermark":2,"has_more":true,"captures":[]
}
```

이 예시는 봉투 구조만 나타낸다. 검증 가능한 전체 record는 [합성 예시](mock_examples.json)의 `backend_*_page`를 사용한다.

1. 처음에는 cursor=0으로 요청하고 첫 응답 watermark를 고정한다.
2. has_more=true이면 **같은 필터·watermark**와 next_cursor로 이어 읽는다.
3. 끝까지 읽었으면 next_cursor를 체크포인트로 저장한다. 새 동기화는 그 cursor에서 watermark를 생략해 새 경계를 받는다.
4. source/start/end를 바꾸면 별도 스트림·커서로 관리한다.
5. 끝 페이지가 비어 있어도 next_cursor는 watermark까지 진행할 수 있다. 필터 밖 수신 순서를 다시 읽을 필요는 없다.

cursor는 관측 시각/record ID가 아니라 테이블별 **커밋된 수신·변경 순번**이다. 뒤늦게 받은 과거 관측과 충돌 수정도 새 cursor를 갖는다. 동일 revision·동일 값을 다시 받아도 최초 received_at을 덮지 않는다. source 쪽은 DB 트랜잭션 커밋 순서대로 단조 증가하도록 보장한다.

현재 `HTTPBackend.sync()`의 초기 기본 범위는 최근 170시간이며 `start`를 한 번 저장한 후 고정한다. 늦게 온 자료를 놓치지 않도록 매 poll마다 start를 현재 시각으로 옮기지 않는다. 더 긴 초기 일괄 이력 제공과 보존 정책은 팀에서 정해야 한다. 전체 2022~2025 연구 원본은 사용자 요청당 전송할 자료가 아니다.

## 3. Station

| 필드 | 타입·조건 | 설명 |
| --- | --- | --- |
| `station_id`, `source_station_id` | string 길이 1~80, 필수 | 내부 공통 ID·공급자 ID |
| `name` | string, 필수 | 표시 이름 |
| `latitude`, `longitude` | number 또는 null, 필수 키 | 범위 33~39 / 124~132 |
| `valid_from`, `received_at` | datetime, 필수 | 현재 자료의 적용 시작·수신 시각 |
| `valid_to` | datetime 또는 null, 기본 null | 종료가 있으면 valid_from보다 뒤 |
| `source` | string, 기본 seoul_bike | 출처 |
| `revision` | string, 필수 | 공급자 또는 수신 기반 버전 |
| `identity_status` | verified/unverified, 기본 verified | verified는 좌표 필수 |
| `validity_basis` | first_seen_receipt / source_effective_interval | 기본 first_seen_receipt |
| `source_valid_from`, `source_valid_to` | datetime 또는 null | 공급자가 제공한 실제 적용 기간만 저장 |

`station_id`는 클라이언트·관측·모델 연결에 쓰는 문자열이다. 예: 현재 API의 `ST-4`가 과거 번호 `102`에 연결되는 것은 검수된 매핑 결과이지 일반 변환 규칙이 아니다. 명칭의 숫자만 추출해 만들지 않는다.

공급자 개소일을 모르면 valid_from은 최초 수신 기반 보수적 적용 구간으로 두고 validity_basis로 구분한다. 좌표 이전·폐소·ID 재사용은 새 revision/적용 구간으로 보존한다. 검증되지 않은 ID·좌표는 unverified로 남겨 예측/추천 대상에서 제외할 수 있어야 한다.

## 4. Observation

| 필드 | 타입·기본 | 설명 |
| --- | --- | --- |
| `source`, `entity_id`, `event_key`, `variable`, `unit` | string, 필수 | 출처·대상·사건키·변수·단위 |
| `value` | number 또는 null, 필수 키 | valid이면 null 불가 |
| `received_at` | datetime, 필수 | 실제 수신 시각 |
| `observed_at`, `issued_at`, `valid_at` | datetime 또는 null | 미제공 시 null. 생성하지 않음 |
| `revision` | string, 기본 0 | 수정 버전 |
| `quality` | valid/missing/invalid, 기본 valid | 누락·비정상·정상 0 구분 |
| `timing_basis` | received / observed_and_received / issued_and_received / historical_proxy | 기본 received |
| `raw_value` | string 또는 null, 길이 ≤160 | 제한된 원래 값. 인증 URL/토큰 금지 |

재고는 `source=seoul_bike`, `variable=inventory`, `unit=bikes`, value는 0 이상 정수다. entity_id는 Station.station_id와 같아야 한다. bikeList에는 실제 관측 시각이 없어 observed_at=null로 둔다. 시각 없는 원본을 received_at으로 관측했다고 위장하지 않는다.

현재 source 예시는 `kma_current`(실황)이며 기상 변수·단위는 T1H/degC, RN1/mm, REH/%, WSD/m/s, PTY/code다. 그리드 entity_id 형식은 `60,127`처럼 `nx,ny`다. 실황의 baseDate/baseTime은 issued_at으로 보존하고 별도 observed_at은 null이다. historical_proxy 자료는 실제 운영 특징에서 사용하지 않는다.

## 5. Forecast

| 필드 | 타입·기본 | 설명 |
| --- | --- | --- |
| `source`, `grid_id`, `variable`, `unit` | string, 필수 | 현재 코드 source=kma_ultra_forecast, 격자 nx,ny |
| `value` | number 또는 null, 필수 키 | valid이면 null 불가 |
| `issued_at`, `valid_at`, `received_at` | datetime, 필수 | 발표·목표·수신 시각을 별도 보존 |
| `revision`, `quality`, `raw_value` | Observation과 동일 | 수정 버전·품질 |

같은 목표 시각의 여러 발표본을 모두 보존한다. 특징 생성은 기준 t 이전의 발표·수신 조건과 목표 정각을 만족하는 자료만 선택한다. 나중에 조회한 과거 API 응답의 received_at은 현재 시각이며 과거 발표 때 수신한 것으로 바꾸지 않는다.

## 6. Capture·충돌·누락

captures의 각 항목은 `capture_id`, `source`, `started_at`, `completed_at`, `expected_rows`, `actual_rows`, `state`, `summary_json`을 갖는다. summary_json은 JSON 객체가 아니라 **문자열**이다. Observation 페이지의 item.capture_id로 연결한다.

서울 전체 재고 조회가 여러 페이지면 모두 모은 뒤 원자적으로 complete 캡처를 공개한다. 한 페이지만 성공한 자료로 직전 완전 캡처를 덮지 않는다. 한 complete 캡처가 API 응답 여러 페이지에 나뉘어도 expected_rows/actual_rows는 **전체 캡처 수량**이다. AI 캐시는 모든 행이 들어올 때까지 importing으로 둔다.

중복 충돌은 item.conflict=true로 전달하고 정상 0대로 바꾸지 않는다. 동일 revision의 서로 다른 값은 충돌로 보존하고 새 변경 cursor를 발행한다. 누락은 value=null/quality=missing이며 실제 0대는 value=0/quality=valid다. 필수 자료 누락으로 모델이 null을 반환하는 것은 정상 업무 응답이다.

## 7. 서버에서 AI 호출 예시

아래 TypeScript는 Node 서버 측 예시다. 프론트에 import하지 않는다. AI/데이터 백엔드의 서로 다른 base URL을 혼동하지 않는다.

```typescript
async function predictStations(stationIds: string[]) {
  const base = process.env.CYCLEON_AI_BASE_URL;
  const token = process.env.CYCLEON_SERVICE_TOKEN;
  if (!base || !token) throw new Error('CycleOn server settings missing');
  const response = await fetch(`${base.replace(/\/$/, '')}/v1/predictions`, {
    method: 'POST',
    headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
    body: JSON.stringify({station_ids: stationIds, horizons_hours: [1, 2, 3]}),
    signal: AbortSignal.timeout(10000), // 애플리케이션 예시값. 서비스 SLA 아님
  });
  if (!response.ok) throw new Error(`CycleOn HTTP ${response.status}`);
  return await response.json(); // 운영 코드는 OpenAPI 기반 타입/런타임 검증 권장
}
```

응답의 null·보류 사유·시각·모델 정보를 프론트로 전달한다. 200+null을 500으로 바꾸거나 확률 누락을 안전으로 변환하지 않는다. 원시 토큰·인증 헤더·외부 공급자 인증 URL은 로그에 남기지 않는다.

## 8. 인계 시 합의할 항목

- 프론트 공개 중계 경로와 사용자 인증, 대여소 목록/좌표 API.
- 실서버 실행 주체·SSH 접속·서비스 토큰 전달·장애 담당자. 현재 listener 부재는 [현황](README.md#현재-사용할-수-있는-것) 참조.
- 팀 데이터 DB와 소스별 수집 주기/계정 한도·재시도·보존 기간.
- 추가 응답 필드나 계약 변경은 PR로 합의하고 코드·OpenAPI·예시를 함께 수정.

자료 계약 구현·모의 호출은 지금 진행할 수 있다. 최종 모델·추천정책·독립평가 결과는 모델 담당이 후속 번들로 전달한다.

## 9. 현재 학습 자료와 백엔드 공급 기준 — 2026-11-02

모델 연구는 확보한 과거 자료로 진행한다. 서비스용 최신 자료의 수집·저장은 백엔드 운영 범위다. 실시간 수집은 현재 모델 연구의 선행 조건이 아니며 이 문서 갱신으로 수집기나 서비스를 재시작하지 않았다.

| 자료·공식 출처 | 확보 방법·현재 모델 사용 | 백엔드 공급 업무 |
| --- | --- | --- |
| [따릉이 시간별 대여가능 수량 OA-22382](https://data.seoul.go.kr/dataList/OA-22382/F/1/datasetView.do) | 분기 ZIP 안 CSV를 다운로드·전량 검수. 2022~2024 학습, 2025상반기 개발. 하반기 정답은 동결 전 미개봉 | 이미 받은 과거 원본을 요청 때마다 전송하지 않는다. 서비스용 정각 재고 이력은 실제 수신 자료로 보존 |
| [대여소 정보 OA-13252](https://data.seoul.go.kr/dataList/OA-13252/F/1/datasetView.do) | XLSX/CSV. 2021.6~2024.12 공식8종을 게시 다음날부터 조건부 적용, 좌표·이웃·기상격자 생성 | API ID·과거 번호·이름·좌표 매핑과 변경 이력을 검수하여 Station 제공 |
| [실시간 대여정보 OA-15493](https://data.seoul.go.kr/dataList/OA-15493/A/1/datasetView.do) | bikeList 소량/수집 시험 이력과 참조 수집기 있음. 현재 상시 수집은 미가동 | stationId, stationName, stationLatitude/Longitude, parkingBikeTotCnt를 서울 전체 완전 캡처로 저장. 키는 서버 환경변수 |
| [기상 실황 파일](https://data.kma.go.kr/data/rmt/rmtList.do?code=400&pgmNo=570)·[초단기예보 파일](https://data.kma.go.kr/data/rmt/rmtList.do?code=410&pgmNo=571) | 인증된 curl ZIP 다운로드·일부 API 대조. 유효한 월/조각은 사용하고 공급자 공백은 결측으로 보존 | 과거 재수신 시각을 과거 실제 수신 시각으로 생성하지 않는다 |
| [기상청 API허브 격자](https://apihub.kma.go.kr/apiList.do?seqApi=10) | 실제 격자 응답과 포털 대조로 kma_completed_hour_v2를 고정. W/F/E 오프라인 특징 및 공유 추론 재생 검증 | 아래 두 raw-grid 제품과 고정 발표본을 받아 같은 파서·표시 정밀도로 공급. 전체 실서비스 연동은 후속 검증 필요 |
| 에어코리아·버스·열차시간표·관광 | 소량 API 응답 확인 이력. 교통·관광은 현 고정모델 학습 입력 미반영. E의 과거 대기질5열은 전부 NaN | 첫 필수 연동 범위에서 제외. 학습·시점·위치 검증 전 현재 값의 소급이나 임의 입력을 추가하지 않음 |

과거 개발 패널은30648시간×역사상3170개 대여소의 결측 포함 격자다. 현재 운영 대여소 개수나 유효 학습행 수와 같지 않다. NEIS·SGIS·DEM은 제외한다.

### 9.1 공급할 원시 값

| 계열 | 필드·표준 값 | 시간·품질 |
| --- | --- | --- |
| 대여소 | station_id, source_station_id, name, latitude, longitude, revision, 적용 기간 | 검증 identity, 실제 수신 시각, 최초수신 적용과 실제 개소일 구분 |
| 재고 Observation | source=seoul_bike, entity_id=station_id, variable=inventory, unit=bikes, value=0이상 정수 | received_at 실제 수신, observed_at은 미제공 시null, capture_id/quality 보존 |
| 실황 Observation | source=kma_current, entity_id="nx,ny", T1H/degC, RN1/mm(1시간누적), REH/%, WSD/m/s, PTY/code | issued_at·received_at 별도, observed_at은 실제 미제공 시null |
| Forecast | source=kma_ultra_forecast, grid_id="nx,ny", 위5변수·단위 | issued_at 발표본, valid_at 목표정각, received_at 실제수신 모두 필수 |

결측은value=null/quality=missing, 실제0은value=0/quality=valid다. JSON에NaN 문자열이나0대 대치를 보내지 않는다. lag/증감/주변 평균/보정/추천 점수는 AI 공유 특징 코드가 생성한다.

### 9.2 현재 기상 모델의 정확한 제품·시각 계약

- 실황: `/api/typ01/cgi-bin/url/nph-dfs_odam_grd`, 예보: `/api/typ01/cgi-bin/url/nph-dfs_vsrt_grd`.
- 변수는T1H/RN1/REH/WSD/PTY의5개다. 현재 POP를 임의 추가하지 않는다. 응답 격자에서 대여소 좌표의 nx,ny를 추출하고 같은 격자를 공유한다. 과거 개발 범위는34격자다.
- 기준정각t의 현재 API 발표본은2024-03-04 10KST 이후 `t-2h+50m`; 이전은 `t-2h`다. 예보는 `t-70m` 발표본의 `t+h` 목표다.
- 예: 기준14:00은 실황12:50 및 예보12:50 발표본에서 목표15/16/17시를 사용한다. 포털 대응은 실황12:00, 예보12:30의lead3/4/5다. 포털 표기·API 발표·목표·실제 수신 시각을 각각 보존한다.
- 모든 운영 입력은 실제received_at<=기준t를 만족해야 한다. 최신 재고14:40을14시 모델 입력으로 넣지 않으며, 화면/추천 자격의 최신 수량으로 별도 전달한다.
- `cycleon/hourly_weather.py`의hourly_job/canonical_grid와 `cycleon/history_weather.py`의parse_grid를 사용한다. 실황 REH는ASCII float64에서floor(x+.5), T1H/RN1/WSD는소수1자리np.round, PTY와예보는해당고정규칙을유지한다. 원본 바이트를 별도 보존한다.
- 검증한 raw-grid 변환을 통과한 자료의revision은 `kma_completed_hour_v2_<원본SHA앞16자>`다. LiveFeatures는계약revision·정확한발표본·수신기준을검사한다. 접두사만붙여일반point제품을같다고선언하지않는다.
- 기존 `Collector.weather()`의 공공데이터포털 point 제품은 이 raw-grid 계약과 별도다. 이전 접근 성공이나 source 문자열 일치만으로 새 W/F/E 운영 연결을 완료 처리하지 않는다. 실제20응답 공유재생은 합성 재고를 쓴306행 코드검증이며 전체 서비스 운영검증은 남아 있다.

### 9.3 검수에서 발견한 문제와 구현 요구

| 발생 문제 | 현재 처리·백엔드 요구 |
| --- | --- |
| 2023재고CSV에헤더없음 | 첫 행을관측으로보존. 파일별헤더판별·CP949/열형식검사 후읽기 |
| 중복키의값충돌·음수/잘못된시각 | 원본·검수건수보존, 동일값중복과값충돌구분, 충돌/비정상은결측/invalid로전달 |
| API ST-ID와과거숫자번호차이·대여소이전 | 명칭숫자만으로추정하지말고공식메타데이터로매핑검증, 좌표revision/적용기간보존 |
| bikeList의list_total_count가페이지건수인응답 | 첫1000건을서울전체로판정하지않음. 모든페이지완료를확인한후원자적complete공개 |
| 일부페이지실패 | 이전정상완전캡처보존, 부분자료로덮어쓰지않음 |
| 포털UTC/API KST, 발표주기변경·표시정밀도차이 | 고정v2변환사용, 발표·목표·수신시각분리. 과거수신시각위장금지 |
| 빈ZIP·헤더만존재·기상날짜충돌 | 실패/공백원본보존, 유효부분사용, 미확보셀은결측. 파일수와기간완료수를혼동하지않음 |
| HTTP200 안의dfs_file_read 오류문자열 | 상태코드이외본문격자형식/개수/수치/결측표식검사. 같은실패키무한재시도금지 |
| 공급자/계정별호출예산불확실 | 소량응답성공과지속수집허용량구분. 전체페이지×주기로예산산정 후팀운영주기확정 |

별도 날짜 대조2568건은 고정오차0.11 이내였고146건에는0이아닌차이가있다. 비교불가152건과원천공백은통과로세지않는다. 역사상실제수신시각·정확한대여소이전시점은미입증이며과거성능은조건부평가다.

### 9.4 첫 인수 검증

1. 모의18012의stations/observations/forecasts를같은페이지계약으로끝까지동기화한다.
2. 팀DB의실제ST-ID 1곳을원천재고및AI코드북과대조한다. 시간은UTC저장/KST표시를일관되게변환한다.
3. 실제재고한완전캡처의expected/actual건수·중복·수신시각을검사한다.
4. 모델담당과기상1격자×5변수×h1/2/3의원본·발표·목표·수신·공유특징값을대조한다.
5. 정규이력은t-168h실제관측및최근24h최소12개관측을요구한다. 초기이력부족은검증된C로전환하고부족을숨기지않는다.
6. 같은AI호출형식으로정상/일부null/추천보류/서버장애를프론트까지연결해확인한다.
