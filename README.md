# P_Project 로컬 이전 전용 브랜치

클라우드 프로젝트 전체 백업을 전달하기 위한 브랜치입니다. 원본 파일은 변경하지 않았습니다.

## 로컬로 가져오기

```bash
git clone --single-branch --branch transfer/local-codex-20261020 https://github.com/Honggyu-Chun/P_Project.git P_Project_transfer
cd P_Project_transfer
python restore_p_project.py --extract
```

Windows에서는 Python 실행 명령으로 `py`, macOS/Linux에서는 `python3`를 쓸 수 있습니다. 추가 패키지는 필요하지 않습니다. 원본 ZIP과 593개 프로젝트 파일을 복원합니다.

로컬 Codex에서 `P_Project_transfer/P_Project` 폴더를 열고 `README.md`와 `docs/local_transfer_guide.md`를 읽도록 요청하세요.

백업에는 소스, 원본 및 처리 데이터, 연구 모델, 시험 예측·보정기, 로그, 문서와 작업 요약을 포함했습니다. 가상환경, 캐시, Git 내부 정보, 비밀 환경설정과 전체 채팅 기록은 제외했습니다.

`backup_parts/` 파일은 원본 ZIP을 순서대로 나눈 조각입니다. 복원 도구가 각 조각과 원본 전체 SHA-256을 확인합니다. 개별 조각을 압축 해제하지 마세요. 기존 P_Project 폴더를 덮어쓰지 않습니다.

모델 선택은 완료됐고 2025년 4월 15일~5월 14일 시험은 이미 평가한 자료입니다. 동일 시험을 새 독립 시험으로 다시 튜닝하지 마세요. 지도 재생은 원래 M1이며, 새 M2 성능은 별도 결과표입니다. 전체 F1 +5%p·전체 추천 유효율 +5%p 목표는 미달이었습니다.
