# 지도 참고 자료

대여소 위치와 거리는 서울시 OA-13252의 2024년 12월 좌표를 사용합니다. 화면의 서울 행정구역 참고도는 `southkorea/seoul-maps`가 공개한 통계청(KOSTAT) 2013년 센서스 경계의 단순화 GeoJSON입니다. 도로 지도·현재의 법적 경계·보행 경로로 사용하지 않습니다. 경계는 예측 모델 입력에 넣지 않습니다.

- 파일: `bike_demo/static/seoul_districts.geojson` (25개 구)
- 원본: https://raw.githubusercontent.com/southkorea/seoul-maps/master/kostat/2013/json/seoul_municipalities_geo_simple.json
- 출처·이용조건 확인: https://github.com/southkorea/seoul-maps/blob/master/README.md
- 원출처: 통계청 2013년 센서스용 행정구역경계
- 공개 형식 변환·단순화: Seoul Maps contributors, Lucy Park
- 저장소 명시 라이선스: Apache License v2.0. 전문은 `docs/templates/MAP_APACHE_2_LICENSE.txt`.
- 확인일: 2026-10-20 KST

경계 GeoJSON은 원본 바이트를 보존했습니다. 화면에서 좌표를 투영해 그리고 구 이름·대여소를 덧붙였습니다. 외부 지도 타일에 의존하지 않으므로 오프라인 체험에서도 같은 지리 참고도를 사용할 수 있습니다. 실제 도로·길은 사용자가 선택한 대여소의 외부 지도 링크로 확인하도록 합니다.
