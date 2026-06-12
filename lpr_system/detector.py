"""
detector.py — 번호판 검출 · 색상 분류 · 전처리 모듈
"""

import threading
import cv2
import numpy as np
from typing import List, Tuple, Optional, Dict

# ── 상수 ──────────────────────────────────────────────────────
HSV_RANGES = {
    "WHITE":       (np.array([0,   0, 170], np.uint8), np.array([180, 50, 255], np.uint8)),
    "GREENYELLOW": (np.array([36,  45,  45], np.uint8), np.array([92, 255, 220], np.uint8)),
    "YELLOW":      (np.array([18,  80, 120], np.uint8), np.array([35, 255, 255], np.uint8)),
    "BLUE":        (np.array([95,  80,  50], np.uint8), np.array([135, 255, 255], np.uint8)),
}
PLATE_TYPE_MAP = {
    "WHITE": "NEW", "GREENYELLOW": "OLD", "YELLOW": "NEW", "BLUE": "NEW",
}

MIN_AREA      = 800
MAX_AREA      = 45000
MIN_ASPECT    = 1.8
MAX_ASPECT    = 6.5
MIN_SOLID     = 0.4
NMS_IOU_TH    = 0.3
NORM_WIDTH    = 320
NORM_HEIGHT   = 80
MIN_FRAME_DIM = 64

# CLAHE: 스레드별 독립 인스턴스 (레이스 컨디션 방지)
_local = threading.local()


def _get_clahe() -> cv2.CLAHE:
    if not hasattr(_local, "clahe"):
        _local.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return _local.clahe


# ── 카메라별 BackgroundSubtractor ─────────────────────────────
_bg_subtractors: Dict[str, cv2.BackgroundSubtractorMOG2] = {}
_bg_lock = threading.Lock()


def get_bg_subtractor(camera_id: str = "default") -> cv2.BackgroundSubtractorMOG2:
    with _bg_lock:
        if camera_id not in _bg_subtractors:
            _bg_subtractors[camera_id] = cv2.createBackgroundSubtractorMOG2(
                history=200, varThreshold=50, detectShadows=False
            )
        return _bg_subtractors[camera_id]


def has_motion(frame: np.ndarray, min_pixels: int = 500,
               camera_id: str = "default") -> bool:
    if frame is None or frame.size == 0:
        return False
    fg = get_bg_subtractor(camera_id).apply(frame)
    return int(cv2.countNonZero(fg)) >= min_pixels


def release_bg_subtractor(camera_id: str) -> None:
    with _bg_lock:
        _bg_subtractors.pop(camera_id, None)


# ── NMS ───────────────────────────────────────────────────────

def _iou(a: Tuple, b: Tuple) -> float:
    ax1, ay1 = a[0], a[1]
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx1, by1 = b[0], b[1]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _nms(candidates: list, iou_threshold: float = NMS_IOU_TH) -> list:
    if not candidates:
        return []
    sorted_c = sorted(candidates, key=lambda x: x[1][2] * x[1][3], reverse=True)
    kept = []
    for cand in sorted_c:
        if all(_iou(cand[1], k[1]) < iou_threshold for k in kept):
            kept.append(cand)
    return kept


# ── 번호판 후보 검출 ───────────────────────────────────────────

def detect_plate_candidates(
    frame: np.ndarray,
) -> List[Tuple[np.ndarray, Tuple[int, int, int, int], np.ndarray]]:
    """
    프레임에서 번호판 후보 (roi, bbox, contour) 리스트 반환.
    contour는 preprocess_for_ocr의 perspective transform에 활용.
    """
    if frame is None or frame.size == 0:
        return []

    h_f, w_f = frame.shape[:2]
    if h_f < MIN_FRAME_DIM or w_f < MIN_FRAME_DIM:
        return []

    clahe    = _get_clahe()
    gray     = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    enhanced = clahe.apply(gray)
    blurred  = cv2.GaussianBlur(enhanced, (5, 5), 0)

    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel  = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    morphed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(morphed, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if not (MIN_AREA < area < MAX_AREA):
            continue

        x, y, w, h = cv2.boundingRect(cnt)
        aspect = w / max(h, 1)
        if not (MIN_ASPECT < aspect < MAX_ASPECT):
            continue

        hull_area = cv2.contourArea(cv2.convexHull(cnt))
        solidity  = area / hull_area if hull_area > 0 else 0
        if solidity < MIN_SOLID:
            continue

        roi = frame[y: y + h, x: x + w]
        if roi.size == 0:
            continue
        candidates.append((roi, (x, y, w, h), cnt))

    return _nms(candidates)


# ── 색상 분류 ─────────────────────────────────────────────────

def classify_plate_color(plate_roi: np.ndarray) -> Tuple[str, str]:
    if plate_roi is None or plate_roi.size == 0:
        return "WHITE", "NEW"

    hsv    = cv2.cvtColor(plate_roi, cv2.COLOR_BGR2HSV)
    h, w   = hsv.shape[:2]
    sample = hsv[h // 4: 3 * h // 4, w // 4: 3 * w // 4]

    scores = {
        color: int(np.sum(cv2.inRange(sample, lo, hi) > 0))
        for color, (lo, hi) in HSV_RANGES.items()
    }
    best = max(scores, key=scores.get)
    if scores[best] < 20:
        best = "WHITE"
    return best, PLATE_TYPE_MAP[best]


# ── OCR 전처리 ────────────────────────────────────────────────

def preprocess_for_ocr(
    plate_roi:   np.ndarray,
    plate_color: str,
    contour:     Optional[np.ndarray] = None,
) -> np.ndarray:
    if plate_roi is None or plate_roi.size == 0:
        return np.zeros((NORM_HEIGHT, NORM_WIDTH), dtype=np.uint8)

    clahe  = _get_clahe()
    warped = plate_roi

    if contour is not None:
        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx  = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) == 4:
            pts   = approx.reshape(4, 2).astype(np.float32)
            rect  = _order_points(pts)
            dst_w = int(max(np.linalg.norm(rect[1] - rect[0]),
                            np.linalg.norm(rect[2] - rect[3])))
            dst_h = int(max(np.linalg.norm(rect[3] - rect[0]),
                            np.linalg.norm(rect[2] - rect[1])))
            if dst_w > 0 and dst_h > 0:
                dst    = np.float32([[0,0],[dst_w,0],[dst_w,dst_h],[0,dst_h]])
                M      = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(plate_roi, M, (dst_w, dst_h))

    resized  = cv2.resize(warped, (NORM_WIDTH, NORM_HEIGHT),
                          interpolation=cv2.INTER_LINEAR)
    gray2    = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    enhanced = clahe.apply(gray2)
    blurred  = cv2.GaussianBlur(enhanced, (3, 3), 0)

    thresh_type = cv2.THRESH_BINARY if plate_color == "BLUE" else cv2.THRESH_BINARY_INV
    _, binary   = cv2.threshold(blurred, 0, 255, thresh_type + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return cv2.dilate(binary, kernel, iterations=1)


def _order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s    = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect
