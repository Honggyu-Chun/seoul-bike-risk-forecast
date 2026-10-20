from pathlib import Path
import csv, json, hashlib
from datetime import datetime, timezone, timedelta

out=Path(__file__).resolve().parent
report=out/'조사보고서.md'
s=report.read_text(encoding='utf-8')
tail=s[s.index('## 모든 후보와 판정'):]
head='''# 인구·혼잡·행사·스포츠·관광·학사 추가 원천 조사

조사일은 2026-10-20이며 시간대는 Asia/Seoul이다. 서울 고정 대여소 2,729개의 입력 t로 정확히 t+1시간 관측 재고 0대 위험을 예측하는 연구를 대상으로 했다. 2025년 1~6월 자료만 개발 대상으로 삼았고, 3·4분기 정답·성과는 열지 않았다. 학습·대형 처리·기존 실험 설정 변경을 하지 않았다. 개인 API 키도 읽지 않았다. KOPIS 차단 경로에는 요청하지 않았다.

## 결론과 우선순위

새 인구 자료의 **같은 시각 실측값을 2025H1 입력에 바로 넣을 근거는 확보하지 못했다.** 우선 검토할 새 실험은 공개가 끝난 과거 생활인구와 S-DoT 자료로 만든 요일·시간 프로필이다. 기존 KBO 최초 일정과 공휴일은 사전 공개 근거가 있어 유지한다. 아래 순위는 기대 성능이 입증됐다는 뜻이 아니라, 자료를 연결하기 쉬운 정도와 남은 검증 부담에 따른 순서다.

| 우선 | 원천 | 지금 확인한 범위 | 모델 부착 전 남은 조건 |
|---|---|---|---|
| 1 | PE02 생활인구 | H1 월파일·수정일·시간/동 스키마. 파일은 추가 키 없이 접근 가능 | 2025 동 경계·코드, 공개 완료 날짜, 파일 수정일 검수. 당시 일별 공개 근거가 없으면 월파일 공개일 이후만 사용 |
| 2 | PE04 S-DoT | 과거 표본 68행, 좌표표 108행, 현재 API의 스키마 대응 | 센서 이동/가동 이력과 중복 처리. 우선 공개 완료 과거 주의 프로필. 같은 시각 관측 입력은 2025 수신/공개 기록이 더 필요 |
| 3 | PE15 대학 학사 | 고려대 2025-02-24 변경공지와 첨부 약 20KB | 해당 공지 이전에 소급 금지. 첨부 교체 여부, 캠퍼스 위치, 2024학년도 1~2월 달력과 대학별 변경공지 확인 |
| 4 | PE07 문화행사·PE11 TourAPI | 보유 서울 행사 재검수 경로, TourAPI 공식 스키마·승인 조건 | 당시 수정본 입증. TourAPI는 별도 개발키 신청 후 작은 2025 표본 확인. 등록일만으로 변경 전 일정을 복원했다고 주장하지 않음 |
| 5 | PE01 실시간 도시데이터 | 현재 인구·혼잡·공급자 예측을 한 응답에서 확인 | 2025 응답 이력이 없어 H1 개발 실험에 직접 추가 불가. 앞으로 실제 수신 시각·장소/추계 버전·장애 기록을 보존 |

PE12 KBO와 PE19 공휴일은 기존 보유 원천이며 새 확보 건수로 세지 않는다. KBO 최초 일정의 일별 노출은 기존 자료로 비교할 수 있으나 경기 시각·순연은 각각 발표 시점의 근거를 추가해야 한다. PE03 생활이동은 현행 2025H1 파일들이 2025-11-25 수정본이라는 제한 때문에 우선순위를 낮췄다.

## 시점과 의미를 구분하는 규칙

1. **관측, 공급자 예측, 사후 집계를 분리한다.** 생활인구는 특정 정시의 추계 인구, S-DoT는 센서 카운트, 생활이동은 출도착 추정 이동이다. 도시데이터의 `FCST_TIME`은 공급자 예측의 유효 시각이다. 어느 것도 실제 따릉이 이용자 수와 같지 않다.
2. **관측·공개·수신 시각을 별도 저장한다.** 등록일은 최초 공개 시각을 보장하지 않고, 파일 수정일은 전체 수정 이력을 복원하지 않는다. 없는 2025 `received_at`을 만들지 않는다. 과거 자료의 조건부 연구에서도 `archived_reception_unknown`을 유지한다.
3. **사전 일정과 사후 결과를 섞지 않는다.** 관중 수·입장객 수·흥행 순위·실제 경기 종료 시각은 t 이후 알려질 수 있다. 취소·순연도 공지 시각이 t 이하인 버전만 사용한다.
4. **2025 공간 버전을 쓴다.** 현재 121장소, 현재 250m 격자, 2026 행정동을 2025에 그대로 소급하지 않는다. 대여소와 당시 영역·센서·행사장 사이의 연결 거리와 범위 밖 여부를 보존한다.
5. **성능은 이후 같은 모델에서 원천별로 비교한다.** 이번에는 모델에 붙이거나 개선 수치를 계산하지 않았다. 기존 개발기간 분할과 정확한 t+1 레이블을 유지해야 한다.

## 소량 실검수에서 확인한 사실

### S-DoT: 과거와 현재 스키마는 대응하지만 공개 시각은 별도

[구형 공식 자료](https://data.seoul.go.kr/dataList/OA-15964/S/1/datasetView.do)의 `S-DoT_WALK_2025.01.06-01.12.csv` 앞 8,192바이트를 저장하고 완전한 68행만 검사했다. 열은 모델번호·센서시리얼·측정시간·지역·자치구·행정동·방문자수·등록일이다. 첫 행의 측정은 2025-01-05 23:54, 등록은 2025-01-06 00:08:03이다. 파일 라벨과 측정일 경계가 완전히 일치하지 않아 원시 시각으로 필터해야 한다.

이 작은 표본에서 측정→등록 차이는 11.07~18.12분이고, 센서×측정시각 중복은 17건이다. 전체 지연이나 중복률을 추정한 결과가 아니다. `REG_DTTM`을 공개 시각으로 쓰지 않는다. 해당 주간 파일의 포털 수정일은 2025-01-20이다. 따라서 단순 lag7일도 이 파일의 공개보다 빠를 수 있다. 먼저 공개가 끝난 과거 주의 요일·시간 프로필을 구성하는 편이 시점 조건을 설명하기 쉽다.

2024-06-20 설치좌표 XLSX는 108행이다. 표본의 0패딩 숫자시리얼을 `방문자 센서코드`와 연결하면 68/68행이 붙는다. 좌표표의 `시리얼번호`는 다른 제조사 문자열이므로 구분한다. 현재 `IotVdata018`의 1행도 같은 측정·등록 열을 반환했다. WiFi MAC 카운트와 CCTV 방식이 섞여 있어 실인구로 해석하지 않으며 센서별 척도·결측·가동 여부가 필요하다. [신형 OA-22832](https://data.seoul.go.kr/dataList/OA-22832/S/1/datasetView.do)는 2026-02-02 공개와 126센서를 안내하므로 2025H1에 소급하지 않는다.

### 생활인구: D+4와 월파일 공개 시점을 구분

[행정동 자료](https://data.seoul.go.kr/dataList/OA-14991/S/1/datasetView.do)는 2025년 1~6월 ZIP을 보유한다. 표시 수정일은 1월 자료가 2월 7일, 5월 자료가 6월 7일, 6월 자료가 7월 7일이다. [현행 공식 FAQ](https://data.seoul.go.kr/dataVisual/seoul/seoulLivingPopulation.do?testcode=0318)는 D+4 공개를 안내한다. 그러나 2025 각 날짜의 당시 API 수신 캐시는 없다. 월파일만 있다면 6월 전체 값을 6월 입력에 쓰면 안 된다. 2024 자료나 직전 공개 월의 프로필을 예측 시점별로 고르는 방식부터 검토한다.

구형 API는 현재 성공했지만 표본 날짜가 2026-07-31이었다. 기존 집계구·행정동 생산 종료와 현행 250m 전환을 구분해야 한다. `TOT_LVPOP_CO`의 소수는 추계 결과이며 `*`는 비식별 값이다.

### 도시데이터: 현재 서비스와 2025 재현을 구분

[공식 가이드](https://data.seoul.go.kr/dataVisual/seoul/guide.do)는 실시간 인구 5분 갱신과 과거 분석 시 생활인구·생활이동 이용을 안내한다. [2025-05-12 공지](https://data.seoul.go.kr/together/notice/boardView.do?seq=8ce4ea450eb8820dddc6302d1e783a27)는 5월 16일 업데이트 후 116→120장소, 인구예측 제공 106장소를 안내한다. 현행 가이드는 2025-09-17 전후 통신사·추계 변경도 구분한다. 현재의 121장소와 산식을 2025에 그대로 적용할 수 없다.

현재 광화문·덕수궁 표본은 `PPLTN_TIME=2026-10-20 18:05`와 19:00 이후 `FCST_TIME`을 반환했다. 예측 유효 시각으로 생성·공개 시각을 역산하지 않는다. 저장 XML은 앞 150KB의 부분 응답이며 추출한 필드는 `city_live_partial_schema.json`에 있다. [공식 메타](https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do)의 이용허락은 공공누리 1유형이다. 현행 실시간 인구 추정에서 90일 이하 단기체류 외국인은 제외된다는 제한도 있다.

### 학사일정: 수정일과 사전 공지를 구분

[NEIS 공식 명세](https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17220190722175038389180&infSeq=2)에 따라 가락고 2025H1 일정 5행을 받았다. 입학식 등의 과거 행이 있지만 `LOAD_DTM=20261020`이라 2025에 알던 일정의 증거가 아니다. 현재 적재일을 최초 등록일로 바꾸면 안 된다.

[고려대 공지](https://ni3.korea.ac.kr/bbs/bigdatascience/159/256513/artclView.do)는 2025-02-24 게시와 2025-02-21 변경본을 명시한다. 실제 첨부 제목은 ‘학사일정(안)’으로 예정 상태를 보존한다. 날짜가 있는 원공지부터 검수하고 현재 대학 달력의 2025 선택값만으로 당시 공개를 입증하지 않는다. 대학별·캠퍼스별 학기 경계도 분리한다.

'''
s=head+tail
# Improve a few dense generated labels without altering evidence.
s=s.replace('CSV는공통21개열로시간/공간해상도·이력·지연·키/승인·라이선스·연결법·가설·판정·다음행동을기록한다.', 'CSV는 공통 21개 열로 시간·공간 해상도, 이력, 지연, 키·승인, 라이선스, 연결법, 가설, 판정, 다음 행동을 기록한다.')
s=s.replace('기존보유자료의가능범위이며, 새로발견한모든데이터가이미모델에준비됐다는뜻이아니다.', '기존 보유 자료의 가능한 범위이며, 새로 발견한 모든 데이터가 이미 모델에 준비됐다는 뜻은 아니다.')
s=s.replace('과거공개시점/수정이력이검증되지않았다는뜻이다.', '과거 공개 시점·수정 이력이 검증되지 않았다는 뜻이다.')
s=s.replace('공식 포털 이용조건 확인 필요; 출처표시; 원천 통신 추계','공공누리 1유형: 출처표시; 원천 통신 추계')
report.write_text(s,encoding='utf-8')

with (out/'후보목록.csv').open(encoding='utf-8-sig',newline='') as f:
    reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
rows[0]['cost_or_license']='공공누리 1유형: 출처표시; 현행 단기체류 외국인 추정 제외'
with (out/'후보목록.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)

audit=json.loads((out/'출처검증.json').read_text(encoding='utf-8'))
audit['candidates'][0]['verified_facts'].extend(['공식 메타의 이용허락은 공공누리 1유형이다.','현행 인구 추계는 90일 이하 단기체류 외국인을 제외한다.'])
audit['candidates'][0]['sample_files'].append('city_dataset.html')
partial={'city_live.xml','sdot_20250106_first8192.bin','sdot_20250106_partial.csv','festival_standard.html','migration.html','migration.txt','stage_standard.html','stage_standard.txt'}
files=[]
for p in sorted(out.iterdir()):
    if p.is_file() and p.name!='출처검증.json':
        b=p.read_bytes();files.append({'file':p.name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'local_file_modified_at':datetime.fromtimestamp(p.stat().st_mtime,timezone(timedelta(hours=9))).isoformat(),'partial':p.name in partial})
audit['local_files']=files
audit['validation']={'candidate_rows':len(rows),'csv_fields':len(fields),'candidate_id_unique':len({r['candidate_id'] for r in rows})==len(rows),'all_primary_urls':all(r['official_url'].startswith('https://') for r in rows),'note':'동일 날짜 자료를 나중에 받은 표본이다. 2025 당시 수신·공개 확인으로 표시하지 않는다.'}
(out/'출처검증.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(audit['validation'],ensure_ascii=False))
