from pathlib import Path
import csv
import json
import hashlib
from datetime import datetime, timezone, timedelta

OUT = Path(__file__).resolve().parent
NOW = datetime.now(timezone(timedelta(hours=9))).isoformat()
FIELDS = 'candidate_id,name,domain,provider,official_url,service_id,temporal_resolution,spatial_resolution,historical_coverage,live_access,publication_or_update_lag,timestamps_available,join_method,effect_hypothesis,evidence_level,key_or_approval,cost_or_license,admissibility,priority,next_action,verified_at'.split(',')
rows = []
evidence = []

def add(cid, name, domain, provider, url, service, time, space, history, live, lag, stamps, join, hypothesis, level, auth, license_, status, priority, next_, sources, verified, limits, samples=()):
    vals = [cid,name,domain,provider,url,service,time,space,history,live,lag,stamps,join,hypothesis,level,auth,license_,status,str(priority),next_,NOW]
    rows.append(dict(zip(FIELDS, vals)))
    evidence.append({'candidate_id':cid,'official_sources':sources,'verified_facts':verified,'limitations':limits,'sample_files':list(samples),'model_effect_evidence':'따릉이 1시간 후 0대 개선 직접 증거 없음; 영향 설명은 검증 전 가설','verified_at':NOW})

add('PE01','서울 실시간 도시데이터·인구·혼잡·인구예측','인구/혼잡','서울특별시',
 'https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do','OA-21285 citydata; OA-21778 citydata_ppltn(동일 원천 가족)',
 '현재 인구 5분; 인구예측 향후 12시간','주요 장소 영역; 현행 121장소','공식 FAQ는 과거 인구 분석에 생활인구/생활이동 이용 안내; 2025 원응답 이력 없음',
 '공개 sample 1장소 응답 앞 150KB 확인; 전체 응답 아님','갱신 5분은 최대 수신 지연 보장이 아님',
 'PPLTN_TIME; FCST_TIME; 로컬 수신시각 별도 필요',
 '당시 장소 경계와 station point-in-polygon/거리 가중; 받은 스냅샷 중 received_at<=t; 인구 관측과 예측 필드 분리',
 '인구 급증/혼잡이 인근 대여·반납 변화에 선행할 가능성; 관측 인구와 공급자 예측은 서로 다른 특징',
 '공식 명세+현재 소량 표본','서울 API 키; sample은 문서 허용 소량; 개인키 미사용','공식 포털 이용조건 확인 필요; 출처표시; 원천 통신 추계',
 '조건부: 앞으로 수집; 2025H1 직접 입력 부적합',5,'2025 당시 스냅샷 존재 문의; 앞으로 received_at과 장소 버전 보존',
 ['https://data.seoul.go.kr/dataVisual/seoul/guide.do','https://data.seoul.go.kr/together/notice/boardView.do?seq=8ce4ea450eb8820dddc6302d1e783a27','https://data.seoul.go.kr/together/notice/datasetNoticeView.do?bbsCd=10008&ditcCd=&pageIndex=1&seq=80d6f0ee7267f416f3f199e85d4da68e','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-21285&srvType=A&infSeq=1'],
 ['2025-05-12 공지는 2025-05-16 업데이트 후 116→120장소 변경과 예측 제공 106장소를 안내한다.','현행 가이드는 2025-09-17 이전과 이후의 통신사/추계 변경을 구분한다.','현재 표본 광화문·덕수궁은 PPLTN_TIME=2026-10-20 18:05, 19:00 이후 FCST_TIME을 반환했다.'],
 ['현재 121장소/산식을 2025 전체에 소급하지 않는다.','예측값의 FCST_TIME은 예측 유효시각이며 생성·공개·수신시각이 아니다.','city_live.xml은 앞 150000바이트 부분응답이다; 재사용 XML 전체로 파싱하면 안 된다.'],
 ['city_api.html','city_guide.html','city_change_20250512.html','city_live.xml','city_live_partial_schema.json'])

add('PE02','서울 생활인구(내국인·장단기 외국인; 행정동/집계구)','인구','서울특별시·KT',
 'https://data.seoul.go.kr/dataList/OA-14991/S/1/datasetView.do','OA-14991 SPOP_LOCAL_RESD_DONG; 외국인 OA-14992/OA-14993; 집계구 OA-14979',
 '일×정시(시간)','2025 행정동/2016 집계구; 현행 250m는 별도 버전','2025H1 월 ZIP 목록 확인; 2024 이하 파일도 존재; 기존 생산 2026-07-31 종료',
 '구형 API sample 1행 성공하지만 2026-07-31 값을 반환: 현재 인구 서비스 아님','현행 공식 FAQ D+4 공개; 월 ZIP은 다음 달 7일 전후 수정일 표시',
 'STDR_DE_ID; TMZON_PD_SE; 월파일 수정일; 개별 최초 공개/수신시각 없음',
 '2025 경계로 대여소→행정동; t 이전 공개 완료 파일/날짜만 사용; lag7d/14d 또는 과거 완료주 요일×시간 프로필',
 '주거·업무·관광 인구의 일중 분포가 대여 수요의 반복성을 보완할 가능성',
 '공식 과거 파일목록+API 스키마 실응답','파일 무키; 현재/소량 API 서울 키 또는 sample','공공누리 1유형; 소수 집계 비식별 *를 0으로 바꾸지 않음',
 '추가작업: 공개 완료 과거값만 조건부 활용; 동시각 입력 부적합',1,'2024-12~2025-05 월파일 중 필요한 소량 범위부터 수집; 파일 수정일 기준 eligibility; 동코드 이력 검수',
 ['https://data.seoul.go.kr/dataList/OA-14991/S/1/datasetView.do','https://data.seoul.go.kr/dataVisual/seoul/seoulLivingPopulation.do?testcode=0318','https://data.seoul.go.kr/dataVisual/seoul/seoulLivingArchive.do','https://data.seoul.go.kr/dataList/OA-14993/S/1/datasetView.do'],
 ['202501 ZIP 수정일 2025-02-07, 202505 ZIP 2025-06-07, 202506 ZIP 2025-07-07을 확인했다.','현행 FAQ는 시간 단위 생산, 4일 후 공개를 안내한다.','API의 실제 열은 날짜·시간대·행정동코드·총생활인구·성연령별 인구이다.'],
 ['2025년에도 정확히 D+4였는지 개별 배포 이력은 미검증. 단순 lag4를 당연히 적격으로 만들지 않는다.','H1 안에서 6월 월파일은 7월 공개이므로 H1 입력에 사용 불가; 일별 API 당시 캐시가 있어야 별도 가능.','월파일 수정일은 최초 공개를 완전히 증명하지 않으므로 archived_reception_unknown 유지.'],
 ['people_api.html','people_live.json','dong_list.html','living_guide.html'])

add('PE03','수도권 생활이동(목적별·수단별)','인구/이동','서울특별시·KT',
 'https://data.seoul.go.kr/dataList/OA-22300/F/1/datasetView.do','OA-22300; OA-22298; 수단별 OA-22655 등 동일 자료 가족',
 '공개 1시간×일; 캠퍼스 20분','서울 행정동 OD; 수도권 상세 250m는 캠퍼스','2025H1 파일 있음; 현행 H1 파일은 2025-11-25 수정본 확인',
 '실시간 OD API로 확인되지 않음; 파일 공개','공개 완료된 월/일 묶음; 최신 목록의 공개 지연과 2025 과거 수정본을 구분',
 '기준일/출도착시간/목적; 파일수정일; 원래 공개시각 미확인',
 '대여소 행정동별 도착·출발·순유입의 이미 공개된 과거 월 요일시간 프로필; 통근/등교 등 목적 분리',
 '통근·통학·관광의 유입/유출 방향이 역세권 따릉이 수요를 보완할 가능성',
 '공식 매뉴얼 안내+과거 파일 목록','행정동 파일 무키; 250m/20분은 서울 빅데이터캠퍼스 이용 신청','공식 포털/캠퍼스 이용 조건; 파일 대량 다운로드 미실시',
 '조건부: 당시 공개 버전 미검증; 실시간 입력 부적합',7,'2025-11-25 재공개 이전 원본/공개일 요청; 없으면 주모델 제외하고 조건부 분석으로만',
 ['https://data.seoul.go.kr/dataVisual/seoul/capitalRegionLivingMigration.do','https://data.seoul.go.kr/dataList/OA-22300/F/1/datasetView.do','https://data.seoul.go.kr/dataList/OA-22298/F/1/datasetView.do'],
 ['공식 안내는 서울 행정동 1시간, 캠퍼스 250m 20분을 구분한다.','7가지 이동목적은 출근·등교·귀가·쇼핑·병원·관광·기타이다.','2025-01 일별 ZIP과 2025-05 월별 ZIP들의 수정일이 2025-11-25로 표시된다.'],
 ['일별 파일이 있다고 당일 공개라는 뜻은 아니다.','원천은 통신 추정 이동이며 따릉이 이용자 수/승하차와 동일하지 않다.'],['migration.html','migration.txt'])

add('PE04','S-DoT 유동인구 센서(구형·신형)','인구/센서','서울특별시',
 'https://data.seoul.go.kr/dataList/OA-15964/S/1/datasetView.do','OA-15964 IotVdata018; 신형 OA-22832(같은 원천 가족)',
 '구형 10분 측정; 주간 CSV; 신형 설명 10분','구형 설명 100센서; 2024 좌표표108행; 신형126센서','구형 2025H1 주간 파일 모두 목록 확인; 신형은 2025-11 이후 및 2026-02 공개',
 'IotVdata018 현재 sample 1행 성공; 최근1개월 제한; 신형 상세명세 미검수','표본 측정→등록 11.07~18.12분; 주간 파일은 주끝 약8일 후 수정; 공개 SLA 아님',
 'SENSING_TIME; REG_DTTM; 파일수정일; 로컬 received_at 별도',
 '0패딩 SERIAL_NO를 숫자 센서코드와 대응 후 좌표 반경250/500m; 센서관측·등록·공개 조건; 측정키 중복 처리',
 '인근 보행 활동의 최근 변화/과거 반복성이 대여 수요를 보완할 가능성',
 '과거 CSV 부분표본+좌표 전체소파일+현재 API 대응 검증','파일 무키; 서울 API 키; sample 소량 허용; 별도 부서 제공 없음','공공누리 1유형; 연구 결과가 서울 공식 입장이 아님 명시',
 '추가작업: 과거 공개 완료 주 프로필 우선; 실시간 과거 복원은 조건부',2,'2025 센서별 가동/이동이력·공개지연 검증; 주간 파일 수정일 이후만 사용하는 프로필; 현재 장기 수신기록 별도',
 ['https://data.seoul.go.kr/dataList/OA-15964/S/1/datasetView.do','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-15964&srvType=A&infSeq=1','https://data.seoul.go.kr/dataList/OA-22832/S/1/datasetView.do'],
 ['2025-01-06~12 주간파일의 앞8192바이트를 저장해 완전한 68행을 확인했다.','표본의 센서 숫자코드→2024-06-20 좌표표 연결 68/68, 측정키 중복17건이다.','현재 API도 MODEL_NM/SERIAL_NO/SENSING_TIME/VISITOR_COUNT/REG_DTTM의 같은 스키마이다.','표본 첫 행은 측정2025-01-05 23:54, 등록2025-01-06 00:08:03: 파일 라벨이 측정일 경계와 완전히 일치하지 않는다.'],
 ['WiFi MAC 카운트와 CCTV 피플카운트 방식이 섞이며 정확한 실인구수가 아니다.','REG_DTTM은 등록일이지 공개시각이 아니다. 2025-01-06~12 파일 수정일은 2025-01-20이다.','표본은 전체 주/전센서 품질을 대표하지 않는다.','신형 OA-22832를 2025H1에도 제공된 실시간 원천으로 소급하지 않는다.'],
 ['sdot_20250106_partial.csv','sdot_20250106_first8192.bin','sdot_historical_sample_audit.json','sdot_locations_20240620.xlsx','sdot_locations_schema_sample.json','sdot_schema_and_sample_check.json','sdot_live.json','sdot_api.html','sdot_list.html'])

add('PE05','서울 체류인구(250m)','인구/체류','서울특별시·KT',
 'https://data.seoul.go.kr/dataList/OA-22892/F/1/datasetView.do','OA-22892; 장단기외국인 같은 가족',
 '체류 시작 시간×체류시간(분)','250m 격자','2025-01-01 이후 보유 안내; 2025 당시 개방은 별도 미검증',
 'Sheet/API는 현재 날짜의 5일 전 자료만','D+5 현재 안내; 체류 후 완성되는 정보',
 '체류시작/체류시간; 게시·수신 이력은 없음',
 '대여소 격자별 이미 공개된 과거의 체류 분포; 미래까지 이어진 총체류시간을 t에 쓰지 않음',
 '업무/주거/여가의 장기체류 특성이 수요형태를 구분할 가능성',
 '공식 정의·필터 명세','파일 무키; 상세는 빅데이터캠퍼스; API 서울 키','공식 포털 조건; 3명 미만 *; 개인정보 비식별',
 '조건부: 2025 당시 개방 미검증; 같은시각 체류시간 입력 부적합',10,'2025 당시 파일/공개시점 입증 전 주모델 제외',
 ['https://data.seoul.go.kr/dataVisual/seoul/seoulStay.do','https://data.seoul.go.kr/dataList/OA-22892/F/1/datasetView.do'],
 ['공식 문서는 시작시각 기준 집계와 중복 집계 가능성을 안내한다.','전후일 연결·직전/직후 위치 보간 등 사후 보정이 명시된다.','2025-01-01 이후 데이터 제공과 현재 D+5 제한을 확인했다.'],
 ['체류의 결과 길이는 미래 행태를 포함할 수 있다.','보유연도와 그 해 공개 여부는 다르다.'],['stay_metadata.html'])

add('PE06','상권분석서비스 길단위인구','인구/상권','서울신용보증재단·서울특별시',
 'https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do','OA-15568 VwsmTrdarFlpopQq',
 '기준분기; 6시간대 구간/요일별 주변합','상권 영역','현행 2021 이후; 2024Q3 1행 응답 확인','조회 API는 과거 분기 집계; 실시간 보행량 아님','메타 갱신주기는 월간; 행 값은 분기; 분기별 최초공개일 미상',
 'STDR_YYQU_CD; 최초공개·수신·버전시각 없음','대여소 상권 폴리곤; 공개 완료된 과거분기의 시간대/요일 프로필만; 요일×시간 결합분포로 오해 금지',
 '상권별 활동 강도가 대여소 고정효과와 교호작용할 가능성','공식 스키마+2024Q3 한행 실응답','서울 API; 파일/포털 조회는 무키','공공누리1유형; 2024 공간표준단위 개편 주의',
 '조건부: 공개일 확인 후 과거 분기 배경변수',8,'2024Q3 원본 공개일·2025 당시 영역 확인; 현재 수정분기 소급 사용 배제',
 ['https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-15568&srvType=A&infSeq=1','https://data.seoul.go.kr/together/notice/datasetNoticeView.do?bbsCd=10008&ditcCd=&pageIndex=1&seq=a2197dabd5a3a52edd6fe0d4eead6bd3'],
 ['STDR_YYQU_CD=20243인 1행이 반환됐고 6시간구간 인구/요일별 인구 열이 있다.','현재 메타는 월간 갱신이지만 실제 조회키는 기준분기이다.','2026-07-03 이후 2021년부터 제공, 2024 공간 단위 변경 안내가 있다.'],
 ['TMZON_00_06_FLPOP_CO 등은 시간별 관측값이 아니다.','각 요일×각 시간대의 공동 분포가 아니므로 임의 곱으로 만들지 않는다.'],['market_api.html','market_2024q3_sample.json'])

add('PE07','서울 문화행사','행사','서울특별시',
 'https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-23041&srvType=A&infSeq=2','OA-23041 aiViewCulture',
 '행사 시작/종료일; 시간은 본문 품질에 따름','행사 좌표/장소','기존 수집19642건; H1겹침1944; 예비적격728(기존 조사 결과)','현재 API 명세 재확인; 신규 대량수집 없음','등록일 존재; 수정 버전 이력 미검증',
 '등록일/행사일; 당시 received_at 없음','좌표 500/1000m; 등록일<=t; 예정 기간 노출; 시간 미상은 일별 특징',
 '문화행사 전후 유입/유출이 인근 0대 위험에 영향 가능','기존 확보+공식 명세 재확인','보유 서울 키; 추가 키 불필요','공식 포털 이용조건; 출처표시; 행사소개 이미지 재배포 별도 주의',
 '조건부: 과거 공개/수정본 미검증',4,'이미 보유 원본의 등록/수정 열 검수; 당시 행사 공지와 대조; 주실험과 분리',
 ['https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-23041&srvType=A&infSeq=2'],
 ['이번에는 공식 명세만 저장했다. 건수는 기존 API와추가데이터.md의 확인 결과를 승계했다.'],
 ['기간 동안 매일 공연 또는 일정시간 관객발생으로 가정하지 않는다.','현재 과거행사 스냅샷은 당시 버전을 증명하지 않는다.'],['culture_docs.html'])

add('PE08','KOPIS 공연·시설','행사/공연','예술경영지원센터',
 'https://kopis.or.kr/por/cs/openapi/openApiInfo.do?menuId=MNU_00077','pblprfr; pblprfr/{mt20id}; prfplc/{mt10id}',
 '공연기간/회차시간 자유문장','공연장 좌표·좌석','기존41페이지3273ID 부분확보; 2025H1 전체아님','기존 키작동 후 RequestBlocked; 이번 호출0회','frstregdt/updatedate 존재; 변경이력 전체아님',
 'frstregdt; updatedate; dtguidance; 로컬 수신기록','시설 좌표 반경/거리; 등록<=t; 회차 해석 가능분만; 좌석은 규모대리',
 '회차 종료·시작 전후 수요 파동 가능; 실제 관객은 사후값','기존 감사기록+공식안내','기존 KOPIS 키; 차단은 제공자에 문의 후 해결; 재호출/우회금지','제공자 API 이용조건; 좌석·공연정보를 관객수로 바꾸지 않음',
 '추가작업: 차단 문의/부분완료; 과거 공개이력 조건부',9,'차단 상태 해결과 원일정/시설 상세 검수 전 학습 부착하지 않음',
 ['https://kopis.or.kr/por/cs/openapi/openApiInfo.do?menuId=MNU_00077'],
 ['명세PDF 5.0(2026-04-23)와 기존 부분수집·차단사실을 작업기록에서 확인했다.','이번에는 공개 안내 페이지만 읽고 KOPIS API 호출하지 않았다.'],
 ['frstregdt/updatedate 필터만으로 변경 전 공연시각 복원 불가.','흥행순위·사후관객·현재상태 금지.'],['kopis_info.html'])

add('PE09','전국문화축제표준데이터','행사/축제','한국관광공사·지방자치단체',
 'https://www.data.go.kr/data/15013104/standard.do','tn_pubr_public_cltur_fstvl_api',
 '행사 시작/종료일','행사장 주소/위경도; 전국 중 서울 추출','현재 합본; 2025 당시본 미검증','포털 소량 미리보기/공식 스키마 확인; API 미호출','메타 분기; 기관등록분 월초 병합으로 시차 안내',
 'fstvlStartDate; fstvlEndDate; referenceDate; 최초공개 없음','서울 좌표 반경500/1000m; 행사기간; 기관별 dated 원공지로 발표<=t 입증',
 '대규모 반복 축제의 지역 수요파동 가능','공식 스키마/합본방식 검증','파일 무키; API 별도 공공데이터포털 활용신청 자동승인/개발10000','공공데이터포털/개별 원본 이용조건 확인; 유료신청 없음',
 '조건부: 2025 당시 공개본 확인 필요',6,'서울만 원기관 2025 파일/공지로 재검증; referenceDate를 공개일로 쓰지 않음',
 ['https://www.data.go.kr/data/15013104/standard.do'],
 ['축제명·기간·장소·주소·좌표·데이터기준일 스키마를 확인했다.','개별기관자료는 월초 병합하므로 시점 차이가 발생한다고 공식 안내한다.'],
 ['현재 합본에 2025 행이 남는지와 당시 공개 여부는 별도.','일정·예정정보만 사용하며 방문인원 사후성과는 제외.'],['festival_standard.html'])

add('PE10','전국공연행사정보표준데이터','행사/공연','지방자치단체',
 'https://www.data.go.kr/data/15013106/standard.do','전국공연행사정보표준데이터 파일/포털',
 '시작/종료일 및 시작/종료시각','행사장 주소/위경도','현재 합본; 2025 당시 스냅샷 미검증','공식 항목/제공범위 확인; 대량다운로드 없음','분기 표기; 기관자료 월초 병합 시차',
 '행사일/시각; 데이터기준일; 최초공개 없음','시간이 있는 서울 단발행사만 인근 대여소×시간; 공지 게시일<=t',
 '세부 시각이 있는 지역 단발공연 전후 수요를 포착 가능','공식 스키마','포털 파일 무키; API 별도 승인·한도는 이번 미검증','개별 제공기관 라이선스 확인 필요',
 '조건부: 당시 원공지 필요',8,'서울 지자체 2025 게시파일/원공지를 우선 확보해 시간 필드 완결성 검수',
 ['https://www.data.go.kr/data/15013106/standard.do'],
 ['정부예산이 투입된 지역주민 대상 단발 공연행사 범위다.','공식 항목은 행사 시작/종료시각·객석수·입장연령·주소·좌표를 포함한다.'],
 ['전국 민간공연을 포괄하지 않는다.','객석수는 실제 참석인원이 아니다.'],['stage_standard.html','stage_standard.txt'])

add('PE11','TourAPI 국문 관광정보 행사검색','행사/관광','한국관광공사',
 'https://www.data.go.kr/data/15101578/openapi.do','KorService2/searchFestival2 (공식 내장 Swagger 확인)',
 '행사 시작/종료일; 세부시간은 상세정보','행사 좌표/주소 전국','공식 시간범위는 미표기; 2025 잔존/당시본 미검증','명세만 확인; 개인 서비스키 호출 없음','공식 메타 실시간; 행사입력/수정 지연 SLA 미확인',
 'createdtime; modifiedtime; eventstartdate; eventenddate; mapx/mapy','서울 좌표 반경; createdtime<=t; modifiedtime>t인 과거행은 버전미상 표시; 문화행사/표준축제와 중복제거',
 '민간·관광축제 누락 보완 가능','공식 내장 Swagger/승인·비용 명세','별도 공공데이터포털 키; 개발 자동승인1000; 운영 심의승인','무료; 포털 이용허락범위 제한없음; 이미지별 Type1/Type3 별도',
 '추가작업: 별도 활용신청 + 과거 공개이력 조건부',6,'키발급 후 1건/짧은범위 검증; 2025 잔존과 원공지 버전 확인',
 ['https://www.data.go.kr/data/15101578/openapi.do'],
 ['공식 서비스는 행사정보를 포함하는 15종 관광정보를 제공한다.','HTML 내 Swagger의 searchFestival2에 createdtime/modifiedtime/행사일/위경도가 있다.','개발 자동승인·운영 심의·개발1000·무료를 확인했다.'],
 ['createdtime은 등록일이지 모든 경로의 최초공개시각을 보장하지 않는다.','현재 수정일은 변경전 일정 내용을 복원하지 못한다.'],['tour_api.html','tour_festival_schema_excerpt.json'])

add('PE12','KBO 2025 최초 정규시즌 일정','스포츠/야구','KBO(공식 스포츠단체; 공공기관 API 아님)',
 'https://www.koreabaseball.com/MediaNews/Notice/View.aspx?bdSe=10341','2024-12-20 보도자료 첨부 원일정',
 '경기 예정일; 시간은 별도 사전발표 필요','잠실/고척 경기장','기존675경기 추출; 잠실135·고척66(연간 원일정); H1만 필터','공식 원첨부 존재; 기존 보유 재사용','2024-12-20 사전발표; 45경기는 미편성',
 '보도자료 날짜; 예정 경기일; 회차·순연 공지 추가 필요','경기장 반경500/1000/2000m; 원일정 일별 노출; 경기시간 미입증이면 시간대 임의부여 금지',
 '경기장 접근/귀가 수요가 인근 자전거 재고에 영향 가능','사전발표 원일정 직접근거; 효과는 가설','키불필요; 기존 보유파일 재사용','KBO 무단전재·재배포 금지 문구; 원파일 공개재배포 안 함',
 '즉시: 기존 보유 사전일정의 H1 노출 실험 가능',1,'보유 첨부 해시·경기장 좌표 검수; 순연/시각은 발표시점별로만 추가',
 ['https://www.koreabaseball.com/MediaNews/Notice/View.aspx?bdSe=10341'],
 ['공식 본문은 2024-12-20 발표, 2025-03-22 개막, 팀당135경기 우선편성과 미편성45경기를 명시한다.','이번에는 기존675경기 원파일을 재수집하지 않았다.'],
 ['최종 개최·관중·취소 결과를 원일정 특징과 섞지 않는다.','기존 경기장 좌표는 대여소 위치 대리값이므로 실제 경기장/출입구 좌표로 검수 필요.'])

add('PE13','K리그 2025 최초 일정','스포츠/축구','한국프로축구연맹(공식 스포츠단체)',
 'https://www.kleague.com/news_view.do?orderBy=seq&page=1&search=%EC%9D%BC%EC%A0%95&seq=91720&viewOption=album','2025-01-10 K리그1 1~33R 사전일정',
 '경기일/예정시각','FC서울 홈 서울월드컵경기장; K리그2 서울E 별도 원일정 필요','2025-01-10 원발표·첨부 존재 확인; 결과표 수집 안 함','웹 원발표; 승인된 공공 API로 확인되지 않음','원발표 2025-01-10; 이후 순연 가능을 명시',
 '발표일; 예정시각; 후속변경 발표일 별도','홈경기장 좌표×반경; 발표<=t 원일정; 보도자료만으로 K리그2 일정 대체금지',
 '경기장 집객의 접근·귀가 파동 가능','공식 원발표; 이용제한 확인','포털 데이터 비보도 복사/저장/배포는 사전허가 문구; 필요 이용허가 문의','공공 API로 무료개방된 자료라고 주장하지 않음',
 '추가작업: 이용허가/원일정 검수 필요',7,'연맹 연구이용 조건 확인 후 원일정 H1만 정리; 후속 공지 시점 반영',
 ['https://www.kleague.com/news_view.do?orderBy=seq&page=1&search=%EC%9D%BC%EC%A0%95&seq=91720&viewOption=album','https://portal.kleague.com/'],
 ['2025-01-10 공식발표는 1~33R 198경기와 2월15일 개막을 안내한다.','AFC·클럽월드컵에 따른 순연 가능과 파이널 추후 공개를 명시한다.','포털에는 비보도 복사·저장·배포·판매 사전허가 문구가 있다.'],
 ['현재 결과페이지는 당시 알려진 일정과 다를 수 있다.','이 작업에서는 원일정 첨부를 내려받지 않았다.'])

add('PE14','서울경찰 오늘의 집회·시위 사전안내','행사/집회','서울특별시경찰청',
 'https://www.smpa.go.kr/user/nd54882.do','오늘의 집회/시위 게시판',
 '일별 안내; 세부시간·장소는 첨부','집회장소/행진노선','현재 게시판 유지 확인; 2025H1 개별글/첨부 미검증','웹 공지; 공공 API 확인 안 됨','현재 목록은 전일/수일전 작성 예가 있음; 작성시각 시분 없음',
 '작성일; 대상일; 게시글 수정이력 미상','게시일 다음날부터 보수적으로 적용; 장소/노선 지오코딩 후 대여소 거리; 신고예정인원은 실제참석과 분리',
 '대규모 집회·도로통제의 이동경로 변화가 인근 대여·반납에 영향 가능','공식 현행 게시목록','키불필요; 2025 자료 제공/재사용 범위 문의 가능','공식 게시판 이용조건 확인; 개인정보/단체 세부정보 최소화',
 '추가작업: 2025 원게시글/첨부 확인',5,'2025H1 게시글 검색/소량 표본 확인; 게시일·수정여부·첨부시간 확보',
 ['https://www.smpa.go.kr/user/nd54882.do'],
 ['현행 목록은 2026-10-20 집회 안내가 2026-10-19 작성, 10월21일 안내가 10월20일 작성된 것을 보여준다.'],
 ['현재의 사전게시 패턴이 2025 모든 집회에서 같았다고 가정하지 않는다.','시각 없는 게시일을 당일00시 공개로 만들지 않는다.'],['police.html','police.txt'])

add('PE15','대학 공식 학사일정·변경공지','학사','서울 소재 대학(표본 고려대·서울대·연세대)',
 'https://ni3.korea.ac.kr/bbs/bigdatascience/159/256513/artclView.do','대학별 웹 공지·원첨부',
 '개강/종강/시험/방학 일·기간','캠퍼스·출입구; 학교별 다른 일정','고려대 2025-02-24 변경공지와 첨부20KB 확인; 다른 대학 현재2025 페이지','웹 공지; 통합 공공 API 없음','게시일·변경일이 있는 공지 우선; 현재 달력만으로 당시본 입증 불가',
 '고려대 공지작성2025-02-24/변경2025-02-21; 현재 첨부변경이력 없음','당시 캠퍼스 좌표 반경500/1000m×학기/시험/방학; 공지 다음날 이후 사용; 1~2월은2024학년도 달력 필요',
 '대학 주변 통학과 시험·방학 패턴 변화가 대여소별 요일시간 효과를 보완할 가능성','공식 dated 공지+소형 XLSX 실검수','키불필요; 대학별 이용조건; 통합수집 미실시','캘린더 사실 추출에 한정; 첨부 원문 재배포 조건 별도',
 '추가작업: 원공지 시점별 정리 후 조건부',3,'고려대 H1 일정부터 공지시점으로버전 분리; 주요 대학으로 확장; 대학축제는 별도 dated 공지',
 ['https://ni3.korea.ac.kr/bbs/bigdatascience/159/256513/artclView.do','https://new.snu.ac.kr/academics/resources/calendar?year=2025','https://ido.yonsei.ac.kr/ido/info03.do'],
 ['고려대 공지는 2025-02-24 게시, 2025-02-21 변경본을 명시하고 원XLSX가 제공된다.','원XLSX는 제목에 학사일정(안)을 포함한다; 예정 상태를 보존한다.','서울대/연세대 현재 2025 달력은 개강/종강/방학·시험 범위를 확인할 수 있다.'],
 ['3월학년도 자료만으로 1~2월 방학을 채우지 않는다.','대학별·캠퍼스별 일정을 하나의 서울 공통달력으로 만들지 않는다.','공지일 이후 첨부 교체 여부는 미검증.'],['ku_calendar.html','ku_calendar_original.xlsx','ku_calendar_rows.json'])

add('PE16','NEIS 학교 학사일정','학사','교육부·시도교육청',
 'https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17220190722175038389180&infSeq=2','SchoolSchedule; schoolInfo',
 '학교×학사일','초중고 학교; 대학 아님','서울 가락고2025H1 5행 표본 성공; LOAD_DTM 전부20261020','키없는 sample5행; 실제 수집은 별도 NEIS 키','매일 적재; LOAD_DTM은 수정일자라 당시 원일정 공개시각과 다름',
 'AA_YMD; AY; EVENT_NM; LOAD_DTM','학교 주소/좌표 반경; 입학/개학/휴업/시험/방학; 13세 미만 직접 대여수요 해석 주의','중고교 통학/휴업의 지역수요 대리 가능; 효과 크기는 미검증',
 '공식 스키마+2025 과거표본','NEIS 별도 인증키; sample키5행 제한; 서울키와 별개','이용허락범위 제한없음; 참고용 명시',
 '조건부: 과거 공개시점 미검증; 추가 키',7,'학교 원공지로 2025 발표본 확인; LOAD_DTM 현재값만 있으면 주실험제외',
 ['https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17220190722175038389180&infSeq=2'],
 ['가락고 학교코드7010057을 schoolInfo로 확인한 후 2025-01~06 학사일정 sample5행을 받았다.','표본에는2025-03-04 입학식 등이 있고 LOAD_DTM=20261020이다.','공식 매일적재/LOAD_DTM 수정일자/이용제한없음을 확인했다.'],
 ['LOAD_DTM은 현재 적재·수정정보이며 2025 사전공개 증거가 아니다.','초등학교 주변을 학생 직접 따릉이 이용효과라고 단정하지 않는다.'],['neis_docs.html','neis_school_sample.json','neis_2025h1_schedule_sample.json'])

add('PE17','한강공원 이용객 현황(검색결과의 실체:2017)','관광/공원','서울특별시 미래한강본부',
 'https://data.seoul.go.kr/dataList/OA-12040/F/1/datasetView.do','OA-12040',
 '연도 집계/1회성','한강공원','실제 제공파일2017년; 2025H1 시간별 아님','실시간 방문 API 아님','파일수정2017-11-17; 메타갱신2024를 관측연도로 착각금지','연도; 메타수정일',
 '사용하더라도 과거공원 규모 배경변수; 이번 예측입력 제외','공원 방문수요 연관 가능하나 이 원천은 오래되고 거침','공식 파일목록/제공연도 확인','파일 무키','무료/공공누리1유형','부적합: 2025H1 1시간 예측 추가입력',99,'수집하지 않음; 2025 이전 공개된 시설/공원 정적자료와 사전행사공지 우선',
 ['https://data.seoul.go.kr/dataList/OA-12040/F/1/datasetView.do','https://www.data.go.kr/data/15051956/fileData.do'],
 ['공식 제목/파일은2017년이며 갱신주기는1회성이다.'],['2025 포털등록일이나2024 메타갱신일을2025 관측 보유로 오인하지 않는다.'])

add('PE18','주요관광지점 입장객 통계','관광/입장객','문화체육관광부·서울특별시',
 'https://data.seoul.go.kr/bsp/wgs/dataView/data300View/476.do','주요관광지점 입장객(자치구별) 통계',
 '공식 해당표 공표 매년','자치구 내 주요관광지점; 관광객 총량 아님','연도별 사후 통계; 2025H1 시간별 현재입력 아님','실시간 입력 경로 아님','공표 매년; 최초공개일 있는 이전연도만 배경변수 가능','기준연도; 표 공표시각 추가 필요',
 '과거 공개완료 지점/구의 관광규모를 정적배경으로만; 2025 실적을2025 예측에 금지','관광집객과 수요연관 가설; 연간실적은 당일 충격 포착불가',
 '공식 통계개요','공개표; 키불필요','서울/문체부 통계 이용조건 출처표시','부적합: 동시각 예측 입력; 과거 배경만 조건부',99,'동시각 입장객 수집대상에서 제외; 이전연도 공개시각 확인 시만 검토',
 ['https://data.seoul.go.kr/bsp/wgs/dataView/data300View/476.do'],
 ['공식 개요는 일반보고통계·매년공표·자치구범위이며 한사람의 여러지점 중복을 경고한다.'],['해당지점 방문수요이며 서울 관광객 총량도 실시간 군중도 아니다.'])

add('PE19','공식 공휴일·임시공휴일 발표일','휴일','한국천문연구원·정부',
 'https://astro.kasi.re.kr/life/post/calendarData?search_year=2025','2025 월력요항/공휴일 발표',
 '일','서울 공통','기존2025H1 발표일 반영본 보유','정적달력/공식발표; API 신규불필요','2025-01-27 임시휴일은2025-01-14 발표 이후; 6월3일은4월8일 이후',
 'holiday_date; announced_at','대여소×t 공통; 오늘/내일/직전일; announced_at<=t','통근·여가 시간구조 변화 보완','기존 검증자료 승계; 이번 최초재검증 아님','키불필요; 보유자료','공식 출처표시','즉시: 기존 검증본 재사용',1,'중복추가 불필요; 기존 발표시점필터 유지',
 ['https://astro.kasi.re.kr/life/post/calendarData?search_year=2025','https://www.korea.kr/news/policyNewsView.do?newsId=148938559','https://www.korea.kr/news/policyNewsView.do?newsId=148941443'],
 ['기존 API와추가데이터.md에 발표일 반영 완료가 기록되어 있다.'],['이 항목은 새로 발견한 원천/새로 확보한 데이터로 세지 않는다.'])

with (OUT/'후보목록.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)

files=[]
for p in sorted(OUT.iterdir()):
    if p.is_file() and p.name not in {'출처검증.json'}:
        b=p.read_bytes()
        files.append({'file':p.name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'local_file_modified_at':datetime.fromtimestamp(p.stat().st_mtime,timezone(timedelta(hours=9))).isoformat(),'partial':p.name in {'city_live.xml','sdot_20250106_first8192.bin','sdot_20250106_partial.csv','festival_standard.html'}})
audit={'verified_at':NOW,'task_scope':'2025H1 input t→t+1h inventory=0 risk; 2729 fixed stations','boundaries':{'training_started':False,'large_data_collection':False,'personal_api_keys_read':False,'kopis_api_calls':0,'q3_q4_labels_or_metrics_opened':False,'writes_outside_dedicated_output':False},'candidate_count':len(rows),'source_family_count_note':'동일 도시데이터 필드는 PE01 하나로 통합; 내외국 생활인구와 신구 S-DoT도 가족으로 묶음. PE07/08/12/19는 기존 원천의 재점검이다.','time_semantics':{'received_at':'원래2025수신시각 없음. 파일mtime은수신시각으로대체하지않음. sdot_historical_sample_audit만실제HTTP수신기록있음','first_publication':'포털수정일/등록일을최초공개로동일시하지않음','current_api':'현재응답확인은2025당시원본증거아님'},'candidates':evidence,'local_files':files}
(OUT/'출처검증.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')

intro='''# 인구·혼잡·행사·스포츠·관광·학사 추가 원천 조사

조사일: 2026-10-20 (Asia/Seoul). 이 보고서는 2025년 1~6월, 서울 고정 2,729개 대여소에서 입력 t로 정확히 t+1시간의 관측 재고 0대를 예측하는 연구의 추가 특징 후보 조사이다. 학습·대형 처리·기존 실험 설정 변경을 하지 않았고, 3·4분기 정답·성과를 열지 않았다. 개인 API 키는 읽지 않았다. 공개 문서와 허용된 sample 1~5행, 과거 CSV 앞 8KB, 좌표/학사 XLSX 각 약20KB만 점검했다. KOPIS 차단 경로에는 요청하지 않았다.

## 결론과 우선순위

새 인구 자료의 **같은 시각 실측값을 2025H1 입력에 바로 넣을 근거는 확보하지 못했다.** 가장 실용적인 새 실험은 공개가 끝난 과거 생활인구와 S-DoT 주간 자료를 이용한 과거 요일·시간 프로필이다. 보유 KBO 최초일정·공휴일은 이미 사전공개 근거가 있어 먼저 유지한다. 아래 순위는 기대 성능이 입증됐다는 뜻이 아니라, 자료 연결의 현실성과 검증 부담 순서다.

| 우선 | 원천 | 지금 가능한 범위 | 모델 부착 전 남은 조건 |
|---|---|---|---|
| 1 | PE02 생활인구 | H1 월파일 존재·수정일·시간/동 스키마 확인, 추가 키 없이 파일 접근 | 2025 경계·동코드, 공개완료 날짜, 파일수정일, 보수적 지연 검수. lag7일도 당시 일별공개 근거가 없으면 월파일 공개일 이후만 사용 |
| 2 | PE04 S-DoT | 2025H1 주간파일·실제68행·좌표108행·현재 API 스키마 대응 확인 | 센서 이동/가동 이력, 중복 측정키 처리, 파일공개일 이후의 과거주 프로필. 같은시각 센서는2025수신/공개 기록 추가 필요 |
| 3 | PE15 대학 학사 | 고려대2025-02-24 공식 변경공지와 첨부 확보 | 2월24일 이전에 소급 금지, 첨부교체 여부, 각 캠퍼스 좌표·2024학년도1~2월 달력·학교별 변경공지 확인 |
| 4 | PE07 문화행사 + PE11 TourAPI | 보유 서울행사 재검수, TourAPI 공식 스키마·별도키/승인 확인 | 문화행사 당시 수정본 입증; TourAPI 개발키 신청 후 소량2025잔존 확인. 등록일만으로 변경전 일정을 복원했다고 주장하지 않음 |
| 5 | PE01 실시간 도시데이터 | 앞으로 운영 입력 후보:현재 인구·혼잡·공급자예측을한응답에서확인 | 2025 과거응답이없어 현재개발실험엔직접추가불가. 앞으로 수신캐시·장소버전·추계버전·장애기록을보존해야함 |

기존 PE12 KBO·PE19 공휴일은 새 원천 확보로 세지 않는다. KBO 최초일정의 일별 노출은 즉시 비교할 수 있으나 경기시각·순연은 각각 사전발표 근거를 추가해야 한다. PE03 생활이동은 이론적 관련성은 높아도 현행2025H1파일이2025-11-25수정본이라우선순위를낮춘다.

## 시점·의미를 구분하는 규칙

1. **관측, 공급자예측, 사후집계를 분리한다.** 생활인구는 특정 정시의 추계 인구, S-DoT는 센서카운트, 생활이동은 출도착 추정 이동이다. 도시데이터 FCST_TIME은 공급자예측의 유효시각이다. 어느 것도 실제 따릉이 이용자 수가 아니다.
2. **값의 시각과 공개·수신시각을 별도 저장한다.** 등록일은 공개시각이 아니고, 파일수정일은 전체 수정 이력을 복원하지 않는다. 2025 실제 received_at은 만들지 않는다. 이미공개된과거자료의조건부연구에서도 archived_reception_unknown 표시를 유지한다.
3. **사전일정과 사후결과를 섞지 않는다.** 원발표 경기·축제·시험일은 일정 특징이며, 관중수·입장객수·흥행순위·실제 경기종료시각은 t 이후 알려질 수 있다. 취소/순연도 공지<=t인 버전만 허용한다.
4. **공간버전을 고정한다.** 현재121장소, 현재250m격자, 2026행정동을2025에그대로소급하지않는다. 대여소좌표→2025영역/센서/행사장 거리표와 연결거리·범위밖 플래그를 보관한다.
5. **검증은 같은 모델 한 원천씩 진행한다.** 이번에는 모델에 붙이거나 개선수치를 계산하지 않았다. 누출방지자료구성을완료한후기존개발기간분할과정확한t+1레이블정의를유지해비교한다.

## 소량 실검수에서 발견한 구체적 사실

### S-DoT: 원자료는 가깝지만 즉시 공개의 증거가 부족함

[구형 공식자료](https://data.seoul.go.kr/dataList/OA-15964/S/1/datasetView.do)의 `S-DoT_WALK_2025.01.06-01.12.csv` 앞8192바이트를 받고 CP949→UTF-8 부분표본으로 보존했다. 완전한68행은 모델번호·센서시리얼·측정시간·지역·자치구·행정동·방문자수·등록일이다. 첫행은 측정2025-01-05 23:54, 등록2025-01-06 00:08:03이다. 파일라벨은 등록주에가까워측정일경계를필터로검수해야한다.

이 표본의 측정→등록 지연은11.07~18.12분이고, 센서×측정시각 중복은17건이다. 이것은 전체 지연·중복률의 추정치가 아니다. `REG_DTTM`을 공개시각으로 취급하지 않는다. 포털은 해당주파일 수정일을2025-01-20으로 표시한다. 일주일 뒤 같은요일 lag7은 이파일공개보다빠를수있어, 우선 파일공개가끝난 과거주를 요일시간별로 집계하는 방식이 더 명확하다.

2024-06-20 설치좌표 XLSX는108행이며, 표본의0패딩 숫자시리얼을 `방문자 센서코드`로 연결하면68/68행이 붙는다. 열이름의 `시리얼번호`는 별도의 제조사 문자열이므로 혼동하지 않는다. 현재 `IotVdata018`1행도 같은관측/등록열을반환했다. 다만WiFi MAC방식과CCTV카운트가섞여있어 실인구수로해석하지않고센서별척도·결측·가동여부를함께쓴다. [신형 OA-22832](https://data.seoul.go.kr/dataList/OA-22832/S/1/datasetView.do)는2026-02-02공개·126센서설명이며2025H1에소급하지않는다.

### 생활인구: D+4와 월파일 공개시점의 차이

[행정동 자료](https://data.seoul.go.kr/dataList/OA-14991/S/1/datasetView.do)는2025년1~6월ZIP을보유한다. 표시수정일은1월→2월7일,5월→6월7일,6월→7월7일이다. [현행 공식FAQ](https://data.seoul.go.kr/dataVisual/seoul/seoulLivingPopulation.do?testcode=0318)는D+4공개를안내한다. 그러나2025각일의일별API응답수신캐시는없다. 월파일만으로구성한다면6월월전체를6월입력에쓰면안된다. 2024자료나직전공개월의프로필을예측시점별로고르는편이검증가능하다.

구형 API가 현재도 성공했지만 표본일은2026-07-31이었다. 2026-07-31기존집계구/행정동 생산종료와현행250m전환을구분해야한다. `TOT_LVPOP_CO`의소수는추계결과이며 `*`는비식별값이다.

### 도시데이터: 실시간과 2025 재현은 별개

[공식 가이드](https://data.seoul.go.kr/dataVisual/seoul/guide.do)는실시간인구5분갱신과과거분석시생활인구·생활이동이용을명시한다. [2025-05-12공지](https://data.seoul.go.kr/together/notice/boardView.do?seq=8ce4ea450eb8820dddc6302d1e783a27)는5월16일업데이트후116→120장소, 인구예측제공106장소를안내한다. 현행가이드는2025-09-17전후의통신사/추계변경도구분한다. 단순히현재121장소이름을2025에매핑할수없다.

소량표본의광화문·덕수궁은 `PPLTN_TIME=2026-10-20 18:05`, `FCST_TIME=19:00`이후의예측을반환했다. 예측의유효시각만으로생성·공개시각을역산하지않는다. 저장된XML은앞150KB만있는부분응답이므로 `city_live_partial_schema.json`으로확인한다.

### NEIS와 대학: 현재 수정본을 사전공개본으로 부르지 않음

[NEIS 공식명세](https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17220190722175038389180&infSeq=2)의sample5행으로가락고2025H1일정을조회했다. 입학식등과거행이있지만 `LOAD_DTM=20261020`이라2025에알던일정의증거가아니다. 현재적재일을등록최초일로간주하는필터는쓸수없다.

[고려대공지](https://ni3.korea.ac.kr/bbs/bigdatascience/159/256513/artclView.do)는2025-02-24게시와2025-02-21변경본을명시하고XLSX를제공한다. 실제첨부제목은‘학사일정(안)’이므로예정정보로보존한다. 이처럼날짜가있는원공지부터점검하고,현재대학달력의2025선택값만으로사전공개를입증하지않는다. 캠퍼스별·연도별학기경계도분리한다.

## 모든 후보와 판정

CSV는공통21개열로시간/공간해상도·이력·지연·키/승인·라이선스·연결법·가설·판정·다음행동을기록한다. ‘즉시’는기존보유자료의가능범위이며, 새로발견한모든데이터가이미모델에준비됐다는뜻이아니다. ‘조건부’는과거공개시점/수정이력이검증되지않았다는뜻이다.

'''

parts=[intro]
for r,e in zip(rows,evidence):
    parts.append(f"### {r['candidate_id']} {r['name']}\n\n")
    parts.append(f"- 공식 원천: [{r['service_id']}]({r['official_url']}) — {r['provider']}\n")
    for label,key in [('시간·공간','temporal_resolution'),('과거 범위','historical_coverage'),('현재 접근','live_access'),('공개/갱신 지연','publication_or_update_lag'),('시각 필드','timestamps_available'),('대여소×시간 연결','join_method'),('영향 가설','effect_hypothesis'),('키·신청','key_or_approval'),('비용·조건','cost_or_license'),('판정','admissibility'),('다음 작업','next_action')]:
        val=r[key]+(' / '+r['spatial_resolution'] if key=='temporal_resolution' else '')
        parts.append(f'- {label}: {val}\n')
    parts.append('- 직접 확인: '+' '.join(e['verified_facts'])+'\n')
    parts.append('- 제한: '+' '.join(e['limitations'])+'\n\n')

parts.append('''## 재개와 보존 파일

`후보목록.csv`는19개후보가족을담고, `출처검증.json`은공식URL·확인한사실·제한·표본파일·SHA-256을담는다. 동일서울도시API의인구/혼잡/공급자예측필드를여러원천으로부풀리지않았다. 생활인구내외국인과S-DoT신구서비스도각각하나의가족으로묶었다. PE07·08·12·19는기존보유원천의재점검이다.

처음부터다시수집할필요없이 `sdot_schema_and_sample_check.json`, `sdot_historical_sample_audit.json`, `people_api.html`, `tour_festival_schema_excerpt.json`, `neis_2025h1_schedule_sample.json`부터열면된다. 웹스냅샷의파일mtime은수신시각보장이아니며2025당시수신시각을대체하지않는다. `city_live.xml`·과거S-DoTCSV·`festival_standard.html`은부분보존표시가있다.

다음실행자는우선PE02와PE04에대해 `source_time`, `source_published_at`(확인가능한경우), `source_file_modified_at`, `received_at`(실제수신시에만), `version_id`, `timing_eligibility`, `missing_reason`을분리하는작은정제설계를만든다. 학습을재개하기전현재CPU학습이끝났는지와부모실험계획을확인한다. 어느후보도성능개선이확인된자료로서술하지않는다.
''')
(OUT/'조사보고서.md').write_text(''.join(parts),encoding='utf-8')
print(json.dumps({'candidates':len(rows),'report':str(OUT/'조사보고서.md'),'csv':str(OUT/'후보목록.csv'),'audit':str(OUT/'출처검증.json'),'personal_keys_read':False},ensure_ascii=False))
