"""
ocr_engine.py — EasyOCR 기반 한국 번호판 문자 인식
"""

import re
import threading
from typing import TYPE_CHECKING, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    import easyocr as _easyocr_type

try:
    import easyocr as _easyocr
    _EASYOCR_AVAILABLE = True
except ImportError:
    _easyocr           = None          # type: ignore[assignment]
    _EASYOCR_AVAILABLE = False

_READER:    Optional["_easyocr_type.Reader"] = None
_INIT_LOCK: threading.Lock                   = threading.Lock()

# ── 번호판 정규식 ─────────────────────────────────────────────
PATTERN_NEW   = re.compile(r"(\d{2,3}[가-힣]\d{4})")
PATTERN_OLD   = re.compile(r"([가-힣]{2}\d{2}[가-힣]\d{4})")
PATTERN_SMALL = re.compile(r"(\d{2}[가-힣]\d{4})")

CONFIDENCE_THRESHOLD = 0.50

_KOR_CHARS = (
    "가나다라마바사아자차카타파하"
    "거너더러머버서어저처커터퍼허"
    "고노도로모보소오조초코토포호"
    "구누두루무부수우주추쿠투푸후"
)
_REGION_SYLLABLES = "".join(set(
    "서울경기인천부산대구광주대전울산강원충북충남전북전남경북경남제주"
))
_ALLOWLIST = "0123456789" + "".join(dict.fromkeys(_KOR_CHARS + _REGION_SYLLABLES))


def init_reader(gpu: bool = False) -> None:
    global _READER
    if not _EASYOCR_AVAILABLE:
        print("[OCR] easyocr 미설치 — pip install easyocr")
        return
    with _INIT_LOCK:
        if _READER is None:
            print("[OCR] EasyOCR 모델 로딩 중...")
            _READER = _easyocr.Reader(["ko", "en"], gpu=gpu, verbose=False)
            print("[OCR] 준비 완료")


def read_plate(
    processed_img: np.ndarray,
    plate_type: str = "NEW",
) -> Tuple[str, float]:
    if _READER is None:
        return "", 0.0
    if processed_img is None or processed_img.size == 0:
        return "", 0.0

    try:
        results = _READER.readtext(
            processed_img,
            detail=1,
            allowlist=_ALLOWLIST,
            paragraph=False,
        )
    except Exception as e:
        print(f"[OCR] 인식 오류: {e}")
        return "", 0.0

    if not results:
        return "", 0.0

    filtered = [
        (text, conf)
        for (_, text, conf) in results
        if conf >= CONFIDENCE_THRESHOLD
    ]
    if not filtered:
        return "", 0.0

    raw_text = "".join(t for t, _ in filtered)
    avg_conf = sum(c for _, c in filtered) / len(filtered)
    cleaned  = re.sub(r"[^가-힣0-9]", "", raw_text.upper())

    return _extract_plate_number(cleaned, plate_type, avg_conf)


def _extract_plate_number(
    cleaned: str,
    plate_type: str,
    confidence: float,
) -> Tuple[str, float]:
    if plate_type == "OLD":
        m = PATTERN_OLD.search(cleaned)
        if m:
            return m.group(), confidence

    for pattern in [PATTERN_NEW, PATTERN_SMALL, PATTERN_OLD]:
        m = pattern.search(cleaned)
        if m:
            return m.group(), confidence

    return "", 0.0
