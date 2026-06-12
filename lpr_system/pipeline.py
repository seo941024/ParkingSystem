"""
pipeline.py — 번호판 인식 파이프라인 (병합본)

사용자 추가:
  - Levenshtein 편집거리 기반 fuzzy 중복 제거
  - 신뢰도 기반 교체 저장 (더 좋은 인식 결과로 갱신)

자동화 추가:
  - 투표(Voting) 버퍼 — 7프레임 모아 최다 득표 텍스트 확정
  - COOLDOWN_SEC 30초
  - 스트림 자동 재연결
  - on_progress 예외 처리
  - None 프레임 가드
"""

import os
import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Optional, Callable

import cv2
import numpy as np

from database import (
    get_connection, insert_plate_log,
    is_whitelisted, is_blacklisted, insert_entry_exit,
    delete_plate_log_by_text,
)
from detector import (
    has_motion, detect_plate_candidates,
    classify_plate_color, preprocess_for_ocr,
    release_bg_subtractor,
)
from ocr_engine import read_plate

# ── 파라미터 ──────────────────────────────────────────────────
PROCESS_EVERY_N = 3      # N 프레임마다 OCR (투표 샘플용)
COOLDOWN_SEC    = 30     # 동일 번호판 재저장 방지 (초)
VOTE_FRAMES     = 7      # 투표에 사용할 프레임 수
VOTE_WIN_RATIO  = 0.4    # 이 비율 이상 득표 시 확정
VOTE_IOU_TH     = 0.3    # 같은 번호판으로 볼 bbox 겹침 임계값
FUZZY_DIST      = 2      # 편집거리 이내 → 같은 차량으로 간주
SAVE_IMAGES     = True

IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plate_images")
LOG_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

os.makedirs(LOG_DIR,   exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

logging.basicConfig(
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, f"system_{datetime.now():%Y%m%d}.log"),
            encoding="utf-8"),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  투표(Voting) 버퍼
# ══════════════════════════════════════════════════════════════

def _bbox_iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0]+a[2], a[1]+a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0]+b[2], b[1]+b[3]
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter / union if union > 0 else 0.0


class VoteBuffer:
    """
    같은 위치의 번호판 후보를 VOTE_FRAMES개 모아서
    최다 득표 텍스트를 확정 결과로 반환.
    """
    def __init__(self):
        self._slots: list[dict] = []
        self._lock  = threading.Lock()

    def _find_slot(self, bbox) -> Optional[int]:
        for i, slot in enumerate(self._slots):
            if _bbox_iou(bbox, slot["bbox"]) > VOTE_IOU_TH:
                return i
        return None

    def feed(self, bbox, text: str, conf: float,
             roi: np.ndarray) -> Optional[dict]:
        with self._lock:
            idx = self._find_slot(bbox)
            if idx is None:
                self._slots.append({
                    "bbox":  bbox,
                    "votes": defaultdict(int),
                    "rois":  {},
                    "confs": defaultdict(list),
                })
                idx = len(self._slots) - 1

            slot = self._slots[idx]
            slot["bbox"] = bbox
            slot["votes"][text] += 1
            slot["confs"][text].append(conf)
            if text not in slot["rois"]:
                slot["rois"][text] = roi

            total = sum(slot["votes"].values())
            if total < VOTE_FRAMES:
                return None

            best_text  = max(slot["votes"], key=slot["votes"].get)
            best_ratio = slot["votes"][best_text] / total
            self._slots.pop(idx)

            if best_ratio < VOTE_WIN_RATIO:
                logger.debug(f"투표 미달 ({best_ratio:.0%}): {dict(slot['votes'])}")
                return None

            avg_conf = sum(slot["confs"][best_text]) / len(slot["confs"][best_text])
            return {
                "text":       best_text,
                "confidence": avg_conf,
                "bbox":       bbox,
                "roi":        slot["rois"].get(best_text),
            }

    def clear(self):
        with self._lock:
            self._slots.clear()


_vote_buffers: dict[str, VoteBuffer] = {}
_vb_lock = threading.Lock()

def _get_vote_buffer(camera_id: str) -> VoteBuffer:
    with _vb_lock:
        if camera_id not in _vote_buffers:
            _vote_buffers[camera_id] = VoteBuffer()
        return _vote_buffers[camera_id]

def _release_vote_buffer(camera_id: str):
    with _vb_lock:
        _vote_buffers.pop(camera_id, None)


# ══════════════════════════════════════════════════════════════
#  Levenshtein 편집거리 기반 fuzzy 중복 제거
# ══════════════════════════════════════════════════════════════

def _levenshtein(a: str, b: str) -> int:
    if a == b:   return 0
    if not a:    return len(b)
    if not b:    return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j]+1, curr[j-1]+1, prev[j-1]+(ca != cb)))
        prev = curr
    return prev[-1]


# {키: {"time": datetime, "conf": float}}
_last_seen: dict[str, dict] = {}
_ls_lock   = threading.Lock()


def _check_and_update(plate_text: str, confidence: float) -> tuple[bool, str, str]:
    """
    (중복여부, 최종채택번호판, 교체된_이전번호판) 반환.
    - 신규          → (False, plate_text, "")            저장
    - 기존보다 신뢰도 높음 → (False, plate_text, old_key)  교체 저장
    - 기존보다 신뢰도 낮음 → (True,  "", "")              무시
    """
    now = datetime.now()
    with _ls_lock:
        # 만료 항목 정리
        if len(_last_seen) > 200:
            expired = [k for k, v in _last_seen.items()
                       if (now - v["time"]).total_seconds() > COOLDOWN_SEC * 5]
            for k in expired:
                del _last_seen[k]

        # 편집거리 FUZZY_DIST 이내 + 쿨다운 이내 항목 탐색
        similar_key = None
        for key, info in _last_seen.items():
            if (now - info["time"]).total_seconds() >= COOLDOWN_SEC:
                continue
            if _levenshtein(plate_text, key) <= FUZZY_DIST:
                similar_key = key
                break

        if similar_key is None:
            _last_seen[plate_text] = {"time": now, "conf": confidence}
            return False, plate_text, ""

        prev_conf = _last_seen[similar_key]["conf"]
        if confidence > prev_conf:
            logger.debug(f"[DEDUP] '{plate_text}'({confidence:.2f}) > '{similar_key}'({prev_conf:.2f}) → 교체")
            del _last_seen[similar_key]
            _last_seen[plate_text] = {"time": now, "conf": confidence}
            return False, plate_text, similar_key
        else:
            logger.debug(f"[DEDUP] '{plate_text}'({confidence:.2f}) <= '{similar_key}'({prev_conf:.2f}) → 무시")
            return True, "", ""


# ── 중복 실행 방지 ─────────────────────────────────────────────
_video_lock    = threading.Lock()
_video_running: dict[str, bool] = {}


# ══════════════════════════════════════════════════════════════
#  단일 프레임 처리 (투표 버퍼 경유)
# ══════════════════════════════════════════════════════════════

def process_frame(
    frame:        np.ndarray,
    frame_number: int,
    camera_id:    str = "CAM_01",
    direction:    str = "BOTH",
    source_file:  str = "",
    conn=None,
    on_detected:  Optional[Callable] = None,
) -> list[dict]:

    if frame is None or frame.size == 0:
        return []

    _close_conn = conn is None
    if _close_conn:
        conn = get_connection()

    vbuf        = _get_vote_buffer(camera_id)
    results_out = []

    try:
        for plate_roi, bbox, contour in detect_plate_candidates(frame):
            plate_color, plate_type = classify_plate_color(plate_roi)
            processed               = preprocess_for_ocr(plate_roi, plate_color, contour)
            plate_text, confidence  = read_plate(processed, plate_type)

            if not plate_text:
                continue

            # ── 투표 버퍼 ──────────────────────────────────────
            confirmed = vbuf.feed(bbox, plate_text, confidence, plate_roi)
            if confirmed is None:
                continue

            final_text = confirmed["text"]
            final_conf = confirmed["confidence"]
            final_roi  = confirmed["roi"]
            final_bbox = confirmed["bbox"]

            # ── fuzzy 중복 체크 ────────────────────────────────
            is_dup, accepted, replaced = _check_and_update(final_text, final_conf)
            if is_dup:
                continue
            final_text = accepted
            if replaced:
                delete_plate_log_by_text(conn, replaced)
                logger.debug(f"[DEDUP] DB에서 이전 오인식 '{replaced}' 삭제")

            timestamp  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            image_path = ""

            if SAVE_IMAGES and final_roi is not None:
                safe_ts    = timestamp.replace(":", "-").replace(" ", "_")
                image_path = os.path.join(IMAGE_DIR, f"{final_text}_{safe_ts}.jpg")
                cv2.imwrite(image_path, final_roi)

            insert_plate_log(
                conn, final_text, plate_color, plate_type, final_conf,
                timestamp, camera_id, image_path, frame_number, source_file,
            )

            whitelisted = is_whitelisted(conn, final_text)
            blacklisted = is_blacklisted(conn, final_text)

            if direction == "BOTH":
                # 마지막 이벤트가 ENTRY면 EXIT, 아니면(없거나 EXIT이면) ENTRY
                last = conn.execute(
                    "SELECT event_type FROM entry_exit_log WHERE plate_text=? ORDER BY id DESC LIMIT 1",
                    (final_text,)
                ).fetchone()
                event = "EXIT" if last and last[0] == "ENTRY" else "ENTRY"
            else:
                event = direction  # "ENTRY" or "EXIT"
            insert_entry_exit(conn, final_text, event, camera_id, image_path)

            status = "BLACKLIST" if blacklisted else ("GRANTED" if whitelisted else "UNKNOWN")
            logger.info(
                f"[{camera_id}] #{frame_number} | {final_text} | "
                f"{plate_type}({plate_color}) | conf={final_conf:.2f} | vote={VOTE_FRAMES}f | {status}"
            )

            result = dict(
                plate_text=final_text, plate_color=plate_color, plate_type=plate_type,
                confidence=final_conf, timestamp=timestamp,
                bbox=final_bbox, whitelisted=whitelisted, blacklisted=blacklisted,
                status=status, image_path=image_path, camera_id=camera_id,
            )
            results_out.append(result)

            if on_detected:
                try:
                    on_detected(result)
                except Exception as e:
                    logger.warning(f"UI 콜백 오류: {e}")

    finally:
        if _close_conn:
            conn.close()

    return results_out


# ══════════════════════════════════════════════════════════════
#  영상 파일 처리
# ══════════════════════════════════════════════════════════════

def process_video(
    video_path:  str,
    camera_id:   str = "CAM_01",
    direction:   str = "BOTH",
    stop_event:  Optional[threading.Event] = None,
    on_detected: Optional[Callable] = None,
    on_progress: Optional[Callable] = None,
) -> int:
    with _video_lock:
        if _video_running.get(camera_id):
            logger.warning(f"[{camera_id}] 이미 처리 중")
            return 0
        _video_running[camera_id] = True

    if not os.path.isfile(video_path):
        logger.error(f"파일 없음: {video_path}")
        with _video_lock:
            _video_running[camera_id] = False
        return 0

    conn = get_connection()
    cap  = None
    try:
        cap   = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise IOError(f"영상 열기 실패: {video_path}")

        total    = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fc       = 0
        detected = 0

        logger.info(f"영상 처리 시작: {os.path.basename(video_path)} | {total}프레임")

        while True:
            if stop_event and stop_event.is_set():
                break
            ret, frame = cap.read()
            if not ret:
                break

            fc += 1
            if fc % PROCESS_EVERY_N != 0:
                continue
            if not has_motion(frame, camera_id=camera_id):
                continue

            results  = process_frame(frame, fc, camera_id, direction,
                                     os.path.basename(video_path), conn, on_detected)
            detected += len(results)

            if on_progress:
                try:
                    on_progress(fc, total, detected)
                except Exception as e:
                    logger.warning(f"progress 콜백 오류: {e}")

        logger.info(f"영상 처리 완료: {detected}건")
        return detected

    except Exception as e:
        logger.exception(f"영상 처리 오류: {e}")
        return 0
    finally:
        if cap:
            cap.release()
        conn.close()
        with _video_lock:
            _video_running[camera_id] = False


# ══════════════════════════════════════════════════════════════
#  실시간 스트림 (자동 재연결 포함)
# ══════════════════════════════════════════════════════════════
_STREAM_RETRY_SEC = 5


def process_stream(
    source:      str,
    camera_id:   str = "CAM_01",
    direction:   str = "BOTH",
    stop_event:  Optional[threading.Event] = None,
    on_detected: Optional[Callable] = None,
    on_frame:    Optional[Callable] = None,
) -> None:
    import time
    src = int(source) if source.isdigit() else source
    logger.info(f"스트림 시작: {source} ({camera_id})")

    while not (stop_event and stop_event.is_set()):
        conn = get_connection()
        cap  = cv2.VideoCapture(src)

        if not cap.isOpened():
            logger.error(f"스트림 열기 실패: {source} — {_STREAM_RETRY_SEC}초 후 재시도")
            cap.release()
            conn.close()
            time.sleep(_STREAM_RETRY_SEC)
            continue

        fc = 0
        try:
            while not (stop_event and stop_event.is_set()):
                ret, frame = cap.read()
                if not ret:
                    logger.warning(f"[{camera_id}] 프레임 끊김 — 재연결 시도")
                    break

                fc += 1

                if on_frame:
                    try:
                        on_frame(frame.copy())
                    except Exception:
                        pass

                if fc % PROCESS_EVERY_N != 0:
                    continue
                if not has_motion(frame, camera_id=camera_id):
                    continue

                process_frame(frame, fc, camera_id, direction,
                              source, conn, on_detected)
        finally:
            cap.release()
            conn.close()

        if stop_event and stop_event.is_set():
            break
        time.sleep(_STREAM_RETRY_SEC)

    _get_vote_buffer(camera_id).clear()
    _release_vote_buffer(camera_id)
    release_bg_subtractor(camera_id)
    logger.info(f"스트림 종료: {source}")


def start_camera_thread(
    source:      str,
    camera_id:   str = "CAM_01",
    direction:   str = "BOTH",
    on_detected: Optional[Callable] = None,
    on_frame:    Optional[Callable] = None,
) -> tuple[threading.Thread, threading.Event]:
    stop_event = threading.Event()
    t = threading.Thread(
        target=process_stream,
        args=(source, camera_id, direction, stop_event, on_detected, on_frame),
        daemon=True, name=camera_id,
    )
    t.start()
    return t, stop_event
