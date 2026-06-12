"""
alert_system.py — 블랙리스트 차량 인식 시 소리 + 팝업 알림

소리:
  - winsound (Windows 기본, 추가 설치 불필요)
  - beep fallback (Linux/Mac)

팝업:
  - Flet 다이얼로그 콜백으로 UI에 전달
"""

import os
import sys
import threading
import logging
from datetime import datetime
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# ── 알림 기록 (중복 방지) ─────────────────────────────────────
_alerted: dict[str, datetime] = {}
_alert_lock = threading.Lock()
ALERT_COOLDOWN_SEC = 60   # 같은 차량 1분 이내 중복 알림 방지


def _is_alert_duplicate(plate_text: str) -> bool:
    now = datetime.now()
    with _alert_lock:
        prev = _alerted.get(plate_text)
        if prev and (now - prev).total_seconds() < ALERT_COOLDOWN_SEC:
            return True
        _alerted[plate_text] = now
    return False


# ── 소리 ─────────────────────────────────────────────────────

def play_alert_sound(repeat: int = 3):
    """경보음 재생 (스레드에서 실행)"""
    def _play():
        try:
            if sys.platform == "win32":
                import winsound
                for _ in range(repeat):
                    winsound.Beep(1000, 300)   # 1000Hz, 300ms
                    winsound.Beep(800,  200)
            else:
                # Linux/Mac: 터미널 beep
                for _ in range(repeat):
                    print("\a", end="", flush=True)
        except Exception as e:
            logger.warning(f"[ALERT] 소리 재생 실패: {e}")

    threading.Thread(target=_play, daemon=True).start()


# ── 메인 알림 함수 ────────────────────────────────────────────

def trigger_alert(
    plate_text:  str,
    camera_id:   str = "",
    timestamp:   str = "",
    on_popup:    Optional[Callable] = None,   # Flet UI 팝업 콜백
):
    """
    블랙리스트 차량 인식 시 호출.
    - 중복 방지 체크
    - 소리 재생
    - UI 팝업 콜백 호출
    """
    if _is_alert_duplicate(plate_text):
        return

    logger.warning(f"[ALERT] 🚨 차단 차량 인식: {plate_text} | {camera_id} | {timestamp}")

    # 소리
    play_alert_sound(repeat=3)

    # UI 팝업
    if on_popup:
        try:
            on_popup({
                "plate_text": plate_text,
                "camera_id":  camera_id,
                "timestamp":  timestamp,
            })
        except Exception as e:
            logger.warning(f"[ALERT] 팝업 콜백 오류: {e}")
