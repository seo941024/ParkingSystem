# 🚗 한국 번호판 인식 시스템

OpenCV + EasyOCR + SQLite 기반 한국 번호판 인식 파이프라인

---

## 지원 번호판

| 종류 | 배경색 | 예시 |
|------|--------|------|
| **신형 (NEW)** | 흰색 | `123가4567` |
| **구형 (OLD)** | 녹색 | `서울 12 나 3456` |
| 신형 사업용 | 흰색 + 노란 줄 | `123가4567` |
| 신형 외교 | 파란색 | `외교 1234` |

---

## 파일 구조

```
lpr_system/
├── database.py      — SQLite DB 연결 · CRUD · 화이트리스트
├── detector.py      — 번호판 검출 · 색상 분류 · 전처리
├── ocr_engine.py    — EasyOCR 래퍼 · 정규식 검증
├── pipeline.py      — 전체 파이프라인 (영상 처리 진입점)
├── query_tool.py    — CLI 조회 · 화이트리스트 관리
├── demo.py          — EasyOCR 없이 동작을 확인하는 데모
├── requirements.txt — 의존 패키지 목록
├── samples/         — 테스트용 영상 파일 보관 위치
├── plate_images/    — 인식된 번호판 이미지 자동 저장
└── logs/            — 날짜별 시스템 로그
```

---

## 설치

```bash
pip install -r requirements.txt
```

> GPU 가속이 필요하면 `requirements.txt` 안의 torch 줄을 CUDA 버전으로 교체하세요.

---

## 실행

### 1) 데모 (EasyOCR 없이 동작 확인)

```bash
cd lpr_system
python demo.py
```

합성 번호판 이미지를 생성해 DB 저장·조회 전 과정을 확인합니다.

### 2) 영상 파일 처리

```bash
python pipeline.py samples/parking.mp4
python pipeline.py samples/parking.mp4 --camera CAM_01 --gpu --show
```

| 옵션 | 설명 |
|------|------|
| `--camera` | 카메라 ID (기본: CAM_01) |
| `--gpu` | EasyOCR GPU 가속 |
| `--show` | 결과 창 표시 (헤드리스 서버에서는 사용 X) |

### 3) 폴더 일괄 처리

```bash
python pipeline.py samples/
```

### 4) DB 조회 / 관리

```bash
# 최근 100건 조회
python query_tool.py all

# 특정 번호판 이력 조회
python query_tool.py plate 123가4567

# 날짜별 조회
python query_tool.py date 2025-06-07

# 통계 (타입·색상별)
python query_tool.py stats

# 화이트리스트 등록
python query_tool.py add 123가4567 홍길동 "검정 소나타"

# 90일 이전 기록 자동 삭제
python query_tool.py purge 90
```

---

## 주요 파라미터 조정

### pipeline.py

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `PROCESS_EVERY_N` | 5 | N 프레임마다 1회 처리 (높을수록 빠름) |
| `COOLDOWN_SEC` | 10 | 동일 번호판 재저장 방지 간격 (초) |
| `SAVE_IMAGES` | True | 번호판 이미지 파일 저장 여부 |

### detector.py

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `MIN_AREA` / `MAX_AREA` | 800 / 40000 | Contour 면적 필터 |
| `MIN_ASPECT` / `MAX_ASPECT` | 1.8 / 6.5 | 가로:세로 비율 필터 |
| `NORM_WIDTH` / `NORM_HEIGHT` | 320 / 80 | OCR 정규화 크기 |

### ocr_engine.py

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `CONFIDENCE_THRESHOLD` | 0.55 | OCR 신뢰도 최소값 |

---

## DB 스키마

### plate_logs

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 |
| plate_text | TEXT | 번호판 텍스트 |
| plate_color | TEXT | WHITE / GREENYELLOW / YELLOW / BLUE |
| plate_type | TEXT | NEW(신형) / OLD(구형) |
| confidence | REAL | OCR 신뢰도 (0~1) |
| timestamp | TEXT | 인식 시각 |
| camera_id | TEXT | 카메라 ID |
| image_path | TEXT | 저장된 이미지 경로 |
| frame_number | INTEGER | 영상 프레임 번호 |
| source_file | TEXT | 원본 영상 파일명 |

### whitelist

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | |
| plate_text | TEXT UNIQUE | 등록 번호판 |
| owner_name | TEXT | 차량 소유자 |
| vehicle_desc | TEXT | 차량 설명 |
| registered_at | TEXT | 등록 일시 |

---

## 파이프라인 전체 흐름

```
영상 파일
   │
   ▼
프레임 추출 (N프레임마다)
   │
   ▼
모션 감지 (BackgroundSubtractor)
   │ 움직임 있을 때만
   ▼
번호판 후보 검출
   CLAHE → GaussianBlur → Otsu 이진화
   → Morphology → Contour → 면적·비율 필터
   │
   ▼
색상 분류 (HSV)
   WHITE(신형) / GREENYELLOW(구형)
   │
   ▼
전처리
   Perspective 보정 → 320×80 정규화
   → CLAHE → Blur → 색상별 이진화 → Dilate
   │
   ▼
EasyOCR 인식
   신뢰도 필터링 → 정규식 검증
   │
   ▼
중복 방지 (10초 쿨다운)
   │
   ▼
SQLite DB 저장 + 이미지 파일 저장
   │
   ▼
화이트리스트 대조 → 로그 출력
```
