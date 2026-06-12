"""
demo.py — 합성 번호판 이미지를 생성해서 파이프라인을 테스트하는 데모 스크립트
EasyOCR 없이도 DB 저장·조회 전 과정을 확인할 수 있습니다.

실행:
  python demo.py
"""

import os
import sys
import cv2
import numpy as np
from datetime import datetime

# 현재 디렉터리를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    init_db, get_connection,
    insert_plate_log, query_all, query_statistics,
    add_whitelist, is_whitelisted,
)
from detector import classify_plate_color, preprocess_for_ocr


# ────────────────────────────────────────────────────────────────
#  합성 번호판 이미지 생성 (테스트용)
# ────────────────────────────────────────────────────────────────

def make_plate_image(
    text: str,
    plate_type: str = "NEW",
    width: int = 320,
    height: int = 80,
) -> np.ndarray:
    """
    테스트용 합성 번호판 이미지 생성.
    NEW  → 흰색 배경 + 검정 텍스트
    OLD  → 녹색(#6ab04c) 배경 + 흰색 텍스트
    """
    if plate_type == "OLD":
        bg_color   = (76, 176, 106)   # BGR: 구형 녹색
        text_color = (255, 255, 255)
    else:
        bg_color   = (240, 240, 240)  # BGR: 흰색
        text_color = (20,  20,  20)

    img = np.full((height, width, 3), bg_color, dtype=np.uint8)

    # 테두리
    border_color = (0, 0, 0)
    cv2.rectangle(img, (2, 2), (width - 3, height - 3), border_color, 2)

    # 텍스트
    font       = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1.4
    thickness  = 3
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    tx = (width  - tw) // 2
    ty = (height + th) // 2
    cv2.putText(img, text, (tx, ty), font, font_scale, text_color, thickness)

    return img


# ────────────────────────────────────────────────────────────────
#  데모 실행
# ────────────────────────────────────────────────────────────────

def run_demo():
    print("=" * 60)
    print("  한국 번호판 인식 시스템 — 데모 (EasyOCR 없음)")
    print("=" * 60)

    # DB 초기화
    init_db()
    conn = get_connection()

    # 화이트리스트 샘플 등록
    add_whitelist(conn, "123가4567", "홍길동", "검정 소나타")
    add_whitelist(conn, "서울12나3456", "김철수", "흰색 K5")
    print("\n[화이트리스트] 2대 사전 등록 완료")

    # 합성 번호판 목록 (번호판 텍스트, 타입)
    test_plates = [
        ("123가4567",   "NEW"),   # 신형 흰색 — 화이트리스트
        ("456나7890",   "NEW"),   # 신형 흰색 — 미등록
        ("서울12나3456", "OLD"),  # 구형 녹색 — 화이트리스트
        ("경기34다5678", "OLD"),  # 구형 녹색 — 미등록
        ("12라3456",    "NEW"),   # 소형/이륜 흰색
    ]

    os.makedirs("plate_images", exist_ok=True)

    print(f"\n{'─'*60}")
    print(f"  {'번호판':^14}  {'타입':^6}  {'색상':^14}  {'허가':^10}")
    print(f"{'─'*60}")

    for plate_text, plate_type in test_plates:
        # 합성 이미지 생성
        img = make_plate_image(plate_text, plate_type)

        # 색상 분류 (실제 이미지 기준)
        plate_color, detected_type = classify_plate_color(img)

        # 전처리 (결과물 확인)
        processed = preprocess_for_ocr(img, plate_color)

        # 이미지 저장
        ts_str     = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        img_path   = f"plate_images/{plate_text.replace(' ', '_')}_{ts_str}.jpg"
        cv2.imwrite(img_path, img)

        # DB 저장 (데모에서는 OCR 생략, 텍스트 직접 입력)
        confidence = 0.98
        timestamp  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insert_plate_log(
            conn,
            plate_text=plate_text,
            plate_color=plate_color,
            plate_type=plate_type,
            confidence=confidence,
            timestamp=timestamp,
            camera_id="CAM_DEMO",
            image_path=img_path,
            frame_number=0,
            source_file="demo",
        )

        # 화이트리스트 확인
        allowed = is_whitelisted(conn, plate_text)
        status  = "✅ GRANTED" if allowed else "🚫 UNKNOWN"

        print(
            f"  {plate_text:^14}  {plate_type:^6}  "
            f"{plate_color:^14}  {status:^10}"
        )

    # ── DB 조회 결과 출력 ─────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  DB 저장 결과 (최근 10건)")
    print(f"{'─'*60}")

    rows = query_all(conn, limit=10)
    for r in rows:
        print(
            f"  [{r['id']:>3}] {r['plate_text']:^14} | "
            f"{r['plate_type']}({r['plate_color']}) | "
            f"{r['timestamp']}"
        )

    # ── 통계 ─────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  번호판 통계")
    print(f"{'─'*60}")
    for r in query_statistics(conn):
        print(f"  {r['plate_type']} / {r['plate_color']:14} → {r['cnt']}건")

    conn.close()

    print(f"\n{'=' * 60}")
    print("  데모 완료! 저장된 이미지: plate_images/")
    print(f"  DB 경로: parking.db")
    print(f"  조회 명령 예시:")
    print(f"    python query_tool.py all")
    print(f"    python query_tool.py plate 123가4567")
    print(f"    python query_tool.py stats")
    print("=" * 60)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    run_demo()
