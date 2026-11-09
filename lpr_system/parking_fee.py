"""
parking_fee.py — 입출차 시간 기반 주차 요금 계산

요금 설정 (기본값, UI에서 변경 가능):
  - 기본 요금: 1,000원 (최초 30분)
  - 추가 요금: 500원 / 10분마다
  - 1일 최대: 15,000원
  - 무료 시간: 10분 이하
"""

from datetime import datetime
from typing import Optional


# ── 기본 요금표 (런타임에서 변경 가능) ────────────────────────
FEE_CONFIG = {
    "free_min":       10,      # 무료 주차 시간 (분)
    "base_fee":     1000,      # 기본 요금 (원)
    "base_min":       30,      # 기본 요금 적용 시간 (분)
    "unit_fee":      500,      # 추가 요금 단위 (원)
    "unit_min":       10,      # 추가 요금 단위 시간 (분)
    "daily_max":   15000,      # 1일 최대 요금 (원)
}


def calc_fee(
    entry_time: str,
    exit_time:  Optional[str] = None,
    config:     dict          = None,
) -> dict:
    """
    주차 요금 계산.

    Args:
        entry_time : "2025-06-11 10:00:00" 형식
        exit_time  : None이면 현재 시각 기준
        config     : FEE_CONFIG 덮어쓰기 (None이면 기본값)

    Returns:
        {
          "entry_time": str,
          "exit_time":  str,
          "duration_min": int,
          "duration_str": str,   # "1시간 23분"
          "fee": int,            # 원
          "fee_str": str,        # "2,500원"
          "is_free": bool,
        }
    """
    cfg = {**FEE_CONFIG, **(config or {})}

    fmt   = "%Y-%m-%d %H:%M:%S"
    entry = datetime.strptime(entry_time, fmt)
    exit_ = datetime.strptime(exit_time,  fmt) if exit_time else datetime.now()

    diff_min = max(0, int((exit_ - entry).total_seconds() / 60))

    # 요금 계산
    if diff_min <= cfg["free_min"]:
        fee     = 0
        is_free = True
    else:
        fee     = cfg["base_fee"]
        over    = max(0, diff_min - cfg["base_min"])
        units   = -(-over // cfg["unit_min"])   # 올림 나눗셈
        fee    += units * cfg["unit_fee"]
        fee     = min(fee, cfg["daily_max"])
        is_free = False

    h, m = divmod(diff_min, 60)
    if h > 0:
        dur_str = f"{h}시간 {m}분" if m else f"{h}시간"
    else:
        dur_str = f"{m}분"

    return {
        "entry_time":    entry_time,
        "exit_time":     exit_.strftime(fmt),
        "duration_min":  diff_min,
        "duration_str":  dur_str,
        "fee":           fee,
        "fee_str":       f"{fee:,}원",
        "is_free":       is_free,
    }


