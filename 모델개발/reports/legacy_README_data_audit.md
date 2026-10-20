# 서울시 따릉이 수요 예측 — 데이터 확인과 서비스 구상

2026-10-19에 공식 파일을 직접 내려받아 검사한 결과와 재현 코드입니다. 완성 웹앱이나 최종 모델 성능 보고서가 아닙니다.

현재 사용자의 방향은 **공연 정보를 필수로 넣지 않고 서울 전역 대여소의 수요 예측을 개선하는 것**입니다. 우선 데이터의 실제 사용 가능성과 서비스·평가 구상까지만 진행합니다.

- **[최신: 서울 전역 데이터·서비스·평가 검토](reports/citywide_feasibility_brief.md)**
- [추가 자료 다운로드 기록](reports/citywide_download_manifest.json)
- [초기 행사 아이디어의 데이터 검증 보고서 — 이전 범위 기록](reports/feasibility_2026-10-19.md)
- [실제 다운로드 기록·해시](reports/download_manifest.json)
- [대여·행사 원본 품질](reports/analysis_run.json)
- [대여가능 수량의 결측 검사](reports/availability_quality.json)
- [제한된 관측시간 예비실험](reports/pilot_observed_hours/conditions.json)

## 실행

작업 경로는 `/workspace/P_Project`입니다. Python 3.12와 uv를 사용합니다.

```bash
cd /workspace/P_Project
export UV_CACHE_DIR=/workspace/.cache/uv
export XDG_CACHE_HOME=/workspace/.cache
uv sync --frozen
uv run --frozen pytest -q
```

패키지 버전은 `uv.lock`에 고정되어 있습니다. 18개 테스트는 **합성 입력으로 구현의 집계·시차·결측 처리 동작을 검증**합니다. 실제 데이터에서 성능 개선을 입증하는 테스트가 아닙니다.

공식 페이지 접근 확인:

```bash
uv run --frozen python scripts/probe_sources.py
```

차단이나 HTTP 오류가 있으면 종료 코드 2를 반환합니다. HTTP 200도 데이터 성공을 뜻하지 않으므로 본문을 확인해야 합니다. 초기에는 프록시 403이었고, 이후 실제 재조회에서는 10개 공식 페이지가 200으로 응답했습니다. 이 과정은 `reports/initial_access.json`, `reports/access_latest.json`에 구분되어 있습니다.

실제 분석에 사용한 파일 확보 및 집계:

```bash
uv run --frozen python scripts/fetch_public.py --only stations_202412 stations_202506 events_json hourly_202501 availability_2025q1
uv run --frozen python scripts/analyze_seoul.py > reports/analysis_run.json
uv run --frozen python scripts/audit_availability.py
uv run --frozen python scripts/pilot.py \
  --panel data/processed/pilot_panel.csv \
  --train-end 2025-01-21 --valid-end 2025-01-26 --test-end 2025-02-01 \
  --latency-hours 1 --output-dir reports/pilot_observed_hours
```

현재 실행에서 검증한 다운로드는 로그인·개인 인증키 없이 제공되는 공식 파일 경로입니다. 약 0.3GB의 원본을 사용합니다. `data/raw`와 `data/processed`는 Git에서 제외되어 있습니다. 다운로드 스크립트는 기존 파일의 SHA-256을 확인해 재사용하며, 일치하지 않는 파일을 덮어쓰지 않습니다. 원본 제공기관이 데이터를 수정할 수 있으므로 새로 받은 파일의 해시·행 수가 이번 보고서와 다르면 새 실행으로 구분하세요.

원천 이력 및 연도별 스키마 표본도 확인하려면:

```bash
uv run --frozen python scripts/fetch_public.py --only trips_202601_sample hourly_202601_sample events_api_sample kopis_guide
uv run --frozen python scripts/audit_data.py inspect data/raw/hourly_202601_sample.csv --output reports/hourly_202601_sample_inspection.json
uv run --frozen python scripts/audit_data.py inspect data/raw/trips_202601_sample.csv --output reports/local_runs/trips_sample.json
```

앞부분 약 1MB 표본은 무작위 표본도, 한 달 전체의 품질 증거도 아닙니다. 원천 이력에는 자전거번호·생년·성별 등이 있어 개별 이동기록 표본은 공개 결과물에 싣지 않습니다.

## 파일별 책임

| 코드 | 수행 작업 |
|---|---|
| `scripts/probe_sources.py` | 공식 페이지 응답·오류·본문 해시 기록 |
| `scripts/fetch_public.py` | `data/downloads.json`의 공개 파일 다운로드, 용량 제한, 부분/전체 구분 |
| `scripts/audit_data.py` | CSV/ZIP/XLSX 표본 열기, 명시한 컬럼으로 청크 집계 |
| `scripts/analyze_seoul.py` | 확인된 실제 서울시 스키마 적용, 행사·대여소 선택, 1월 전체 검사, 지도용 좌표·시간 패널·그래프 생성 |
| `scripts/audit_availability.py` | 재고 관측의 49시간 결측 및 대여량과의 차이 확인 |
| `scripts/link_events.py` | 다른 지역으로 확장할 때 명시적인 대여소 유효기간·행사 회차 표의 거리 연결 |
| `scripts/pilot.py` | 시간순 분할, B0와 B1-lite 비교, MAE/RMSE·대여소별 결과·추론 시간 저장 |

다른 파일을 집계할 때는 먼저 `inspect`로 컬럼·인코딩을 확인합니다. 예시의 컬럼명은 **실제로 확인한 2025년 1월 파일**에 해당합니다.

```bash
uv run --frozen python scripts/audit_data.py inspect data/raw/bike_hourly_202501.csv \
  --time-col 대여일자 --hour-col 대여시간 --station-col 대여소번호 \
  --output reports/local_runs/hourly_inspection.json
uv run --frozen python scripts/audit_data.py aggregate data/raw/bike_hourly_202501.csv \
  --encoding cp949 --mode counts --station-col 대여소번호 --time-col 대여일자 \
  --hour-col 대여시간 --count-col 이용건수 --output data/processed/all_observed_hours.csv
```

`--mode trips`는 한 행이 한 건의 대여인 원천 이력에만 사용합니다. 집계 결과에는 원본에 있는 대여소·시간 조합만 있습니다. 빠진 시간을 0으로 채우거나 동일한 행을 자동 삭제하지 않습니다.

## 예비실험의 입력 계약과 제한

`pilot_panel.csv`의 `time`은 서울 시각, 예측 구간 `[time,time+1h)`의 시작입니다. `rentals`는 성사된 대여 건수입니다. `coverage_verified=1`은 이번 보수적 실험에서는 **해당 시간에 실제 집계 원본 행이 존재한다**는 의미입니다. 대여소가 월 전체에 계속 운영됐다는 보증이 아닙니다. 행이 없는 시간의 `rentals`는 결측, 플래그는 0입니다.

1시간 지연 가정은 과거 재생의 시뮬레이션 설정입니다. 실제 서울시 월별 파일이 당시 1시간 만에 공개됐다는 뜻이 아닙니다. 직전 이용량은 `t-2h`, 전일 동일 시각은 `t-24h`, 전주 동일 시각은 `t-168h`를 사용합니다. 이동평균은 과거의 알려진 값만 사용하고 관측 개수를 함께 넣습니다. 검증·시험 중 앞선 시각의 관측도 가정한 지연 이후에만 쓰는 rolling one-hour 평가입니다.

현재 B1-lite에는 기상·공휴일이 없습니다. 계획서의 전체 B1과 구별해야 합니다. 최종 실행의 학습/검증/시험은 979/666/103개이며, 전체 대여소·시간 성능이 아닙니다. `recorded_start_window`의 반대 구간은 **확인한 단일 공연의 시간창 밖**이지 실제 행사가 없다는 뜻이 아닙니다.

`--event-features` 옵션은 준비되어 있지만 실제 행사 M1 실험에는 사용하지 않았습니다. 쓰려면 행별 `event_available_at`, `event_coverage_verified`, `event_window` 및 실제 숫자 특징이 있어야 합니다. 그 값은 증거로 확인해야 하며, 통과시키기 위해 임의로 날짜나 1을 채우면 안 됩니다. 이번 2026년 행사 내보내기의 `신청일`만으로는 2025년 당시 일정 버전이 입증되지 않습니다.

## 다음 데이터 작업

1. 대여 0건과 누락·운영중단을 구분하는 마스크를 출처 정의와 추가 자료로 확정합니다. 재고 파일도 결측이 있으므로 대체 정답으로 쓰지 않습니다.
2. 서울 ASOS 108의 2025년 원본 8,760시간은 후속 검토에서 확보했습니다. 관측시각·공개시각·공란·품질 규칙을 구분하고 필요한 기간으로 확장합니다. [검증 결과](reports/citywide_weather_verification.json)를 참고하세요.
3. 공식 공휴일 목록을 보완하고, 주변 대여소 패턴 등 정확도 개선 후보와 기본 모델의 공정한 비교를 구상합니다. 현재 방향에는 행사·KOPIS가 필수가 아닙니다.
4. 기간 확장 및 최종 분할은 새로운 성능 결과를 보기 전에 고정합니다. 이번 1월 자료는 이미 탐색에 사용했으므로 최종 미사용 시험집합으로 재활용하지 않습니다.

각 클라우드 작업은 이미 격리되어 있습니다. 기존 체크아웃을 사용하고, 사용자가 명시적으로 요청하지 않는 한 Git worktree를 만들지 않습니다.
