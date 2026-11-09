"""
app.py — Flet 기반 번호판 인식 시스템 UI (파스텔 톤 에디션)
실행: python app.py
"""

import os
import sys
import base64
import threading
from datetime import datetime

import cv2
import flet as ft

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db, get_connection,
    query_all, query_by_plate, query_by_date,
    query_total_count, query_today_count,
    query_by_date_range, query_entry_exit_range,
    query_hourly_stats, query_entry_exit_hourly,
    get_all_whitelist, add_whitelist, delete_whitelist,
    get_all_blacklist, add_blacklist, delete_blacklist,
    get_all_cameras, upsert_camera, delete_camera,
    get_current_parked,
    delete_plate_log, delete_plate_log_by_text,
    delete_entry_exit_by_text, manual_exit,
)
from pipeline import start_camera_thread, process_video
from ocr_engine import init_reader
from excel_export import export_plate_logs, export_entry_exit
from alert_system import trigger_alert
from parking_fee import calc_fee, FEE_CONFIG

# ── 🎨 파스텔 톤 색상 팔레트 ──────────────────────────────────
C_BG        = "#F1F5F9"  # 메인 콘텐츠 배경
C_SURFACE   = "#FFFFFF"  # 카드 & 테이블 배경
C_SURF2     = "#1E3A5F"  # 사이드바/헤더 배경 (다크 블루)
C_ACCENT    = "#3B82F6"  # 활성 포인트 (Primary)
C_GREEN     = "#10B981"  # 성공/등록 (Success)
C_RED       = "#EF4444"  # 오류/차단 (Error)
C_YELLOW    = "#F59E0B"  # 주의/주차중 (팔레트에 없으나 기능상 호환되는 앰버 색상 추가)
C_TEXT      = "#1E293B"  # 기본 텍스트
C_MUTED     = "#64748B"  # 보조 텍스트
C_BORDER    = "#E2E8F0"  # 테두리 (라이트 테마에 맞게 밝은 회색으로 조정)
 
# 번호판 전용 라벨 색상
C_LABEL_NEW = "#14B8A6"  # 신형 라벨 (Teal)
C_LABEL_OLD = "#4B5563"  # 구형 라벨 (Dark Gray)


# ── 공통 위젯 헬퍼 ────────────────────────────────────────────

def status_color(status: str) -> str:
    return {"GRANTED": C_GREEN, "BLACKLIST": C_RED, "UNKNOWN": C_YELLOW}.get(status, C_MUTED)


def plate_badge(plate_type: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(
            "신형" if plate_type == "NEW" else "구형",
            size=10, color="white", weight=ft.FontWeight.W_600,
        ),
        bgcolor=C_ACCENT if plate_type == "NEW" else "#b3b3b3",
        border_radius=4, padding=ft.padding.symmetric(3, 6),
    )


def make_field(label, hint="", width=None, expand=False, value="") -> ft.TextField:
    return ft.TextField(
        label=label, value=value, hint_text=hint,
        width=width, expand=expand,
        border_color=C_BORDER, focused_border_color=C_ACCENT,
        color=C_TEXT, label_style=ft.TextStyle(color=C_MUTED),
        text_size=13,
    )


def log_row(r: dict, on_delete=None) -> ft.Container:
    status = "BLACKLIST" if r.get("is_black") else ("GRANTED" if r.get("is_white") else "UNKNOWN")
    color  = status_color(status)
    label  = {"GRANTED": "등록", "BLACKLIST": "차단", "UNKNOWN": "미등록"}.get(status, "미등록")

    row_controls = [
        # 💡 [변경] 투박한 세로 선 대신, 깔끔한 작은 원형 점(Dot)으로 상태 표시
        ft.Icon(ft.Icons.CIRCLE, color=color, size=8),
        
        # 번호판 텍스트 자간과 크기 소폭 조정
        ft.Text(r.get("plate_text", ""), size=15, weight=ft.FontWeight.W_600,
                color=C_TEXT, width=120),
        plate_badge(r.get("plate_type", "NEW")),
        
        # 요소들 사이에 살짝 투명한 구분선 추가
        ft.VerticalDivider(width=1, color=C_BORDER),
        
        ft.Text(r.get("camera_id", ""),   size=13, color=C_MUTED, width=80),
        ft.Text(f"{float(r.get('confidence', 0)):.0%}", size=13, color=C_MUTED, width=48),
        ft.Text(r.get("timestamp", ""),   size=13, color=C_MUTED, expand=True),
        
        # 우측 상태 태그 디자인을 더 부드럽게 변경
        ft.Container(
            content=ft.Text(label, size=11, color="white", weight=ft.FontWeight.W_600),
            bgcolor=color, border_radius=4, padding=ft.padding.symmetric(4, 10),
        ),
    ]
    if on_delete:
        row_controls.append(
            ft.IconButton(
                ft.Icons.DELETE_OUTLINE, icon_color=C_RED,
                tooltip="오인식 삭제", on_click=on_delete,
                icon_size=18
            )
        )
        
    return ft.Container(
        content=ft.Row(row_controls, spacing=14,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=C_SURFACE, 
        border_radius=8,
        padding=ft.padding.symmetric(10, 16),  # 💡 위아래/좌우 여백을 넓혀서 답답함 해소
        border=ft.border.all(1, "#EAEAEA"),     # 테두리를 더 흐리고 연하게 변경
        margin=ft.margin.only(bottom=6),       # 항목 간의 간격을 벌림
        
        # 💡 [추가] 은은한 웹 대시보드 느낌의 그림자 효과 부여
        shadow=ft.BoxShadow(
            blur_radius=4,
            color="#00000008",
            offset=ft.Offset(0, 2)
        )
    )

# ══════════════════════════════════════════════════════════════
def main(page: ft.Page):
    # ── 폰트 및 테마 설정 ────────────────────────────────────
    page.fonts = {
        "MyCustomFont": "/fonts/MyFont.ttf" # assets 폴더 내의 상대 경로
    }
    page.theme = ft.Theme(font_family="MyCustomFont") # 전체 UI 기본 폰트로 적용
    
    page.title         = "한국 번호판 인식 시스템"
    page.theme_mode    = ft.ThemeMode.LIGHT # 화사한 톤에 맞게 라이트 모드로 변경
    page.bgcolor       = C_BG
    page.window_width  = 1600
    page.window_height = 900
    page.padding       = 0

    init_db()
    init_reader(gpu=False)
    conn = get_connection()

    # ── 전역 상태 ──────────────────────────────────────────────
    camera_threads: dict[str, tuple] = {}  # {camera_id: (thread, stop_event)}
    alert_list:     list[dict]       = []
    _stop_tick      = threading.Event()
    current_idx     = {"v": 0}

    # ── Ref ───────────────────────────────────────────────────
    content_ref     = ft.Ref[ft.Column]()
    header_time_ref = ft.Ref[ft.Text]()
    card_total      = ft.Ref[ft.Text]()
    card_today      = ft.Ref[ft.Text]()
    card_parked     = ft.Ref[ft.Text]()
    card_cameras    = ft.Ref[ft.Text]()
    log_lv          = ft.Ref[ft.ListView]()
    alert_lv        = ft.Ref[ft.ListView]()

    # ── 공통 함수 ─────────────────────────────────────────────
    def snack(msg: str, color: str = C_GREEN):
        page.snack_bar = ft.SnackBar(
            ft.Text(msg, color="white"), bgcolor=color, duration=2500)
        page.snack_bar.open = True
        try:
            page.update()
        except Exception:
            pass

    def section_title(text: str) -> ft.Text:
        return ft.Text(text, size=20, weight=ft.FontWeight.W_600, color=C_TEXT)

    def stat_card(label, ref, icon, color) -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Row([ft.Icon(icon, color=color, size=26),
                        ft.Text(label, size=13, color=C_MUTED)], spacing=8),
                # 💡 숫자를 감싸는 Container를 추가해서 왼쪽으로 34px 밀어줍니다!
                ft.Row(
                    [ft.Text("0", size=30, weight=ft.FontWeight.W_700,
                                    color=C_TEXT, ref=ref)],
                    alignment=ft.MainAxisAlignment.END
                )
            ], spacing=6),
            bgcolor=C_SURFACE, border_radius=12,
            padding=ft.padding.all(18),
            border=ft.border.all(1, C_BORDER),
            expand=True,
        )

    # ── 대시보드 갱신 (스레드 안전) ────────────────────────────
    def _refresh_dashboard():
        try:
            if card_total.current:
                card_total.current.value   = str(query_total_count(conn))
                card_today.current.value   = str(query_today_count(conn))
                card_parked.current.value  = str(len(get_current_parked(conn)))
                card_cameras.current.value = str(
                    sum(1 for c in get_all_cameras(conn) if c["enabled"]))

            if log_lv.current:
                rows = query_all(conn, limit=50)
                log_lv.current.controls = [log_row(dict(r)) for r in rows]

            if alert_lv.current:
                alert_lv.current.controls = [
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.WARNING_ROUNDED, color=C_RED, size=16),
                            ft.Column([
                                ft.Text(a["msg"], size=13, color="white"),
                                ft.Text(f"{a['camera']}  {a['ts']}", size=11, color="#CBD5E1"),
                            ], spacing=1, expand=True),
                        ], spacing=8),
                        bgcolor=C_SURF2, border_radius=8,
                        padding=ft.padding.all(10),
                        border=ft.border.all(1, C_RED + "88"),
                        margin=ft.margin.only(bottom=3),
                    )
                    for a in alert_list[:20]
                ]

            page.update()
        except Exception:
            pass

    # ── 블랙리스트 팝업 다이얼로그 ───────────────────────────────
    def _show_blacklist_popup(info: dict):
        """블랙리스트 차량 인식 시 UI 팝업 (스레드에서 호출)"""
        def _do():
            try:
                dlg = ft.AlertDialog(
                    modal=True,
                    title=ft.Row([
                        ft.Icon(ft.Icons.WARNING_ROUNDED, color=C_RED, size=28),
                        ft.Text("차단 차량 인식!", color=C_RED,
                                size=18, weight=ft.FontWeight.W_700),
                    ], spacing=10),
                    content=ft.Column([
                        ft.Text(f"번호판: {info['plate_text']}",
                                size=22, weight=ft.FontWeight.W_700, color=C_TEXT),
                        ft.Text(f"카메라: {info['camera_id']}", size=14, color=C_MUTED),
                        ft.Text(f"시각:   {info['timestamp']}",  size=14, color=C_MUTED),
                    ], spacing=6, tight=True),
                    actions=[
                        ft.TextButton("확인", on_click=lambda e, d=dlg: (
                            setattr(d, "open", False),
                            page.update()
                        )),
                    ],
                    bgcolor=C_SURFACE,
                )
                page.dialog      = dlg
                page.dialog.open = True
                page.update()
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()

    # ── 파이프라인 콜백 ────────────────────────────────────────
    def on_detected(result: dict):
        if result.get("blacklisted"):
            alert_list.insert(0, {
                "msg":    f"차단 차량: {result['plate_text']}",
                "ts":     result["timestamp"],
                "camera": result["camera_id"],
            })
            # 소리 + 팝업
            trigger_alert(
                plate_text  = result["plate_text"],
                camera_id   = result["camera_id"],
                timestamp   = result["timestamp"],
                on_popup    = _show_blacklist_popup,
            )
        if current_idx["v"] == 0:
            threading.Thread(target=_refresh_dashboard, daemon=True).start()

    # ── 헤더 시각 1분마다 갱신 ────────────────────────────────
    def _tick():
        import time
        while not _stop_tick.wait(60):
            try:
                if header_time_ref.current:
                    header_time_ref.current.value = datetime.now().strftime("%Y-%m-%d %H:%M")
                    page.update()
            except Exception:
                break

    threading.Thread(target=_tick, daemon=True).start()

    # ══════════════════════════════════════════════════════════
    # 페이지 빌더
    # ══════════════════════════════════════════════════════════

    # ── 대시보드 ───────────────────────────────────────────────
    def build_dashboard():
        current_idx["v"] = 0
        rows = query_all(conn, limit=50)

        parked_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=3, expand=True)

        def refresh_parked():
            parked_col.controls = []
            for p in get_current_parked(conn):
                p = dict(p)
                plate = p["plate_text"]

                def mk_exit(pt=plate):
                    def _exit(e):
                        manual_exit(conn, pt)
                        snack(f"{pt} 출차 처리됨", C_ACCENT)
                        refresh_parked()
                        _refresh_dashboard()
                        page.update()
                    return _exit

                parked_col.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.LOCAL_PARKING, color=C_ACCENT, size=16),
                        ft.Text(plate, size=13, weight=ft.FontWeight.W_700,
                                color=C_TEXT, width=110),
                        ft.Text(p.get("owner_name") or "-", size=12,
                                color=C_MUTED, expand=True),
                        ft.Text(p.get("entry_time", "")[-8:], size=11,
                                color=C_MUTED, width=60),
                        ft.ElevatedButton(
                            "출차", icon=ft.Icons.LOGOUT,
                            bgcolor=C_ACCENT, color="white",
                            height=30, on_click=mk_exit(),
                        ),
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor=C_SURFACE, border_radius=8,
                    padding=ft.padding.symmetric(6, 10),
                    border=ft.border.all(1, C_BORDER),
                    margin=ft.margin.only(bottom=3),
                ))
            try:
                page.update()
            except Exception:
                pass

        refresh_parked()

        return ft.Column([
            section_title("대시보드"),

            # 상단 통계 카드
            ft.Row([
                ft.Row([
                    stat_card("전체 인식",   card_total,   ft.Icons.CAMERA_ALT_OUTLINED,   "#1f1e1e"),
                    stat_card("오늘 인식",   card_today,   ft.Icons.TODAY_OUTLINED,        C_ACCENT),
                    stat_card("주차 중",     card_parked,  ft.Icons.LOCAL_PARKING_OUTLINED, "#d13b42"),
                ], spacing=12, expand=3),
                stat_card("활성 카메라", card_cameras, ft.Icons.VIDEOCAM_OUTLINED, C_GREEN),
            ], spacing=12),

            # 하단 3분할
            ft.Row([
                # 최근 인식 로그
                ft.Container(
                    content=ft.Column([
                        ft.Text("최근 인식", size=14, color=C_MUTED,
                                weight=ft.FontWeight.W_600),
                        ft.ListView(
                            controls=[log_row(dict(r)) for r in rows],
                            expand=True, spacing=0, ref=log_lv,
                        ),
                    ], spacing=8, expand=True),
                    bgcolor=C_SURFACE, border_radius=12,
                    padding=ft.padding.all(14),
                    border=ft.border.all(1, C_BORDER),
                    expand=3,
                ),
                ft.Column([
                    # 주차 중 차량
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Icon(ft.Icons.LOCAL_PARKING, color=C_ACCENT, size=18),
                                ft.Text("주차 중", size=14, color=C_ACCENT,
                                        weight=ft.FontWeight.W_700),
                            ], spacing=6),
                            parked_col,
                        ], spacing=8, expand=True),
                        bgcolor=C_SURFACE, border_radius=12,
                        padding=ft.padding.all(14),
                        border=ft.border.all(1, "#200F6B"),
                        expand=True,
                    ),
                    # 경보
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Icon(ft.Icons.WARNING_ROUNDED, color=C_RED, size=18),
                                ft.Text("경보", size=14, color=C_RED,
                                        weight=ft.FontWeight.W_700),
                            ], spacing=6),
                            ft.ListView(controls=[], expand=True, spacing=0, ref=alert_lv),
                        ], spacing=8, expand=True),
                        bgcolor=C_SURFACE, border_radius=12,
                        padding=ft.padding.all(14),
                        border=ft.border.all(1, C_RED),
                        expand=True,
                    ),
                ], spacing=12, expand=1),
            ], spacing=12, expand=True),
        ], spacing=14, expand=True)

    # ── 기록 조회 ─────────────────────────────────────────────
    def build_search():
        current_idx["v"] = 1
        PAGE_SIZE = 50
        state = {"offset": 0, "mode": "all", "value": ""}

        search_tf = make_field("번호판 검색", "예) 123가4567", expand=True)
        date_tf   = make_field("날짜 검색", "YYYY-MM-DD", width=180)
        result_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=3)
        info_text  = ft.Text("", size=12, color=C_MUTED)

        def _render(rows, offset=0):
            result_col.controls = []
            for r in rows:
                rd = dict(r)
                def mk_del(row=rd):
                    def _del(e):
                        delete_plate_log(conn, row["id"])
                        delete_entry_exit_by_text(conn, row["plate_text"])
                        snack(f"{row['plate_text']} 로그 삭제됨", C_RED)
                        load(state["offset"])
                        page.update()
                    return _del
                result_col.controls.append(log_row(rd, on_delete=mk_del()))
            total = len(rows)
            info_text.value = (
                f"검색 결과 {total}건" if state["mode"] != "all"
                else f"{offset + 1} ~ {offset + total}건"
            )
            page.update()

        def load(offset=0):
            state["offset"] = offset
            m = state["mode"]
            v = state["value"]
            if m == "plate":
                rows = query_by_plate(conn, v)
            elif m == "date":
                rows = query_by_date(conn, v)
            else:
                rows = query_all(conn, limit=PAGE_SIZE, offset=offset)
            _render(rows, offset)

        def do_search_plate(e=None):
            val = search_tf.value.strip()
            if not val:
                snack("번호판을 입력하세요", C_YELLOW)
                return
            state.update(mode="plate", value=val, offset=0)
            load()

        def do_search_date(e=None):
            val = date_tf.value.strip()
            if not val:
                snack("날짜를 입력하세요 (예: 2025-06-07)", C_YELLOW)
                return
            state.update(mode="date", value=val, offset=0)
            load()

        def do_all(e=None):
            search_tf.value = ""
            date_tf.value   = ""
            state.update(mode="all", value="", offset=0)
            load()

        def do_prev(e):
            if state["mode"] == "all":
                load(max(0, state["offset"] - PAGE_SIZE))

        def do_next(e):
            if state["mode"] == "all":
                load(state["offset"] + PAGE_SIZE)

        def do_manual_exit(e=None):
            val = search_tf.value.strip()
            if not val:
                snack("번호판을 먼저 검색하세요", C_YELLOW)
                return
            # 정확히 일치하는 번호판만 출차
            rows = query_by_plate(conn, val)
            if not rows:
                snack("해당 번호판 기록이 없습니다", C_YELLOW)
                return
            plate = dict(rows[0])["plate_text"]
            manual_exit(conn, plate)
            snack(f"{plate} 출차 처리됨", C_ACCENT)
            page.update()

        search_tf.on_submit = do_search_plate
        date_tf.on_submit   = do_search_date
        load(0)

        return ft.Column([
            section_title("기록 조회"),
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        search_tf,
                        ft.ElevatedButton("번호판 검색", icon=ft.Icons.SEARCH,
                                          on_click=do_search_plate,
                                          bgcolor=C_ACCENT, color="white"),
                        ft.Container(width=16),
                        date_tf,
                        ft.ElevatedButton("날짜 검색", icon=ft.Icons.CALENDAR_TODAY,
                                          on_click=do_search_date,
                                          bgcolor=C_ACCENT, color="white"),
                        ft.Container(width=16),
                        ft.TextButton("전체 보기", icon=ft.Icons.REFRESH,
                                      on_click=do_all),
                        ft.Container(width=16),
                        ft.ElevatedButton(
                            "수동 출차", icon=ft.Icons.LOGOUT,
                            on_click=do_manual_exit,
                            bgcolor=C_ACCENT, color="white",
                            tooltip="검색된 번호판을 출차 처리",
                        ),
                    ], spacing=8),
                ], spacing=0),
                bgcolor=C_SURFACE, border_radius=12,
                padding=ft.padding.all(14),
                border=ft.border.all(1, C_BORDER),
            ),
            ft.Row([
                info_text,
                ft.Container(expand=True),
                ft.TextButton("◀ 이전", on_click=do_prev),
                ft.TextButton("다음 ▶", on_click=do_next),
            ]),
            result_col,
        ], spacing=12, expand=True)

    # ── 화이트리스트 ───────────────────────────────────────────
    def build_whitelist():
        current_idx["v"] = 2
        plate_tf = make_field("번호판 *", width=140)
        owner_tf = make_field("소유자",   width=110)
        desc_tf  = make_field("차량 설명", width=140)
        phone_tf = make_field("연락처",   width=130)
        memo_tf  = make_field("메모",     expand=True)
        list_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=3)

        def refresh():
            list_col.controls = []
            for r in get_all_whitelist(conn):
                r = dict(r)
                def mk_del(pt=r["plate_text"]):
                    def _del(e):
                        delete_whitelist(conn, pt)
                        snack(f"{pt} 삭제됨", C_RED)
                        refresh()
                        page.update()
                    return _del

                list_col.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Text(r["plate_text"],        size=14, weight=ft.FontWeight.W_700,
                                color=C_TEXT, width=130),
                        ft.Text(r.get("owner_name",""),   size=13, color=C_MUTED, width=100),
                        ft.Text(r.get("vehicle_desc",""), size=13, color=C_MUTED, width=130),
                        ft.Text(r.get("phone",""),         size=13, color=C_MUTED, width=120),
                        ft.Text(r.get("memo",""),          size=12, color=C_MUTED, expand=True),
                        ft.Text(r.get("registered_at","")[:10], size=11, color=C_MUTED, width=90),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=C_RED,
                                      on_click=mk_del(), tooltip="삭제"),
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor=C_SURFACE, border_radius=8,
                    padding=ft.padding.symmetric(7, 12),
                    border=ft.border.all(1, C_BORDER),
                    margin=ft.margin.only(bottom=3),
                ))

        def do_add(e):
            pt = plate_tf.value.strip()
            if not pt:
                snack("번호판을 입력하세요", C_RED)
                return
            ok = add_whitelist(conn, pt, owner_tf.value, desc_tf.value,
                               phone_tf.value, memo_tf.value)
            if ok:
                snack(f"{pt} 등록 완료", C_GREEN)
                for tf in [plate_tf, owner_tf, desc_tf, phone_tf, memo_tf]:
                    tf.value = ""
            else:
                snack("이미 등록된 번호판입니다", C_YELLOW)
            refresh()
            page.update()

        refresh()
        return ft.Column([
            section_title("화이트리스트 (등록 차량)"),
            ft.Container(
                content=ft.Column([
                    ft.Text("신규 등록", size=13, color=C_MUTED),
                    ft.Row([plate_tf, owner_tf, desc_tf, phone_tf, memo_tf,
                            ft.ElevatedButton("등록", icon=ft.Icons.ADD,
                                              on_click=do_add,
                                              bgcolor=C_GREEN, color="white")],
                           spacing=8),
                ], spacing=10),
                bgcolor=C_SURFACE, border_radius=12,
                padding=ft.padding.all(14), border=ft.border.all(1, C_BORDER),
            ),
            ft.Row([
                ft.Text("번호판", size=12, color=C_MUTED, width=130),
                ft.Text("소유자", size=12, color=C_MUTED, width=100),
                ft.Text("차량설명", size=12, color=C_MUTED, width=130),
                ft.Text("연락처",  size=12, color=C_MUTED, width=120),
                ft.Text("메모",    size=12, color=C_MUTED, expand=True),
                ft.Text("등록일",  size=12, color=C_MUTED, width=90),
            ], spacing=8),
            list_col,
        ], spacing=12, expand=True)

    # ── 블랙리스트 ─────────────────────────────────────────────
    def build_blacklist():
        current_idx["v"] = 3
        plate_tf  = make_field("번호판 *", width=160)
        reason_tf = make_field("차단 사유", expand=True)
        list_col  = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=3)

        def refresh():
            list_col.controls = []
            for r in get_all_blacklist(conn):
                r = dict(r)
                def mk_del(pt=r["plate_text"]):
                    def _del(e):
                        delete_blacklist(conn, pt)
                        snack(f"{pt} 차단 해제", C_GREEN)
                        refresh()
                        page.update()
                    return _del

                list_col.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Container(width=4, height=36, bgcolor=C_RED, border_radius=2),
                        ft.Icon(ft.Icons.BLOCK, color=C_RED, size=18),
                        ft.Text(r["plate_text"], size=14, weight=ft.FontWeight.W_700,
                                color=C_RED, width=130),
                        ft.Text(r.get("reason",""),   size=13, color=C_MUTED, expand=True),
                        ft.Text(r.get("registered_at","")[:10], size=11, color=C_MUTED, width=90),
                        ft.IconButton(ft.Icons.REMOVE_CIRCLE_OUTLINE, icon_color=C_GREEN,
                                      on_click=mk_del(), tooltip="차단 해제"),
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor=C_SURFACE, border_radius=8,
                    padding=ft.padding.symmetric(7, 12),
                    border=ft.border.all(1, C_RED + "55"),
                    margin=ft.margin.only(bottom=3),
                ))

        def do_add(e):
            pt = plate_tf.value.strip()
            if not pt:
                snack("번호판을 입력하세요", C_RED)
                return
            ok = add_blacklist(conn, pt, reason_tf.value)
            if ok:
                snack(f"{pt} 차단 등록", C_RED)
                plate_tf.value = ""
                reason_tf.value = ""
            else:
                snack("이미 차단된 번호판입니다", C_YELLOW)
            refresh()
            page.update()

        refresh()
        return ft.Column([
            section_title("블랙리스트 (차단 차량)"),
            ft.Container(
                content=ft.Column([
                    ft.Text("신규 차단 등록", size=13, color=C_MUTED),
                    ft.Row([plate_tf, reason_tf,
                            ft.ElevatedButton("차단 등록", icon=ft.Icons.BLOCK,
                                              on_click=do_add,
                                              bgcolor=C_RED, color="white")],
                           spacing=8),
                ], spacing=10),
                bgcolor=C_SURFACE, border_radius=12,
                padding=ft.padding.all(14), border=ft.border.all(1, C_RED + "44"),
            ),
            list_col,
        ], spacing=12, expand=True)

    # ── 카메라 설정 ────────────────────────────────────────────
    def build_cameras():
        current_idx["v"] = 4
        cid_tf  = make_field("카메라 ID *", "CAM_02", width=110)
        name_tf = make_field("이름",       "정문 카메라", width=130)
        loc_tf  = make_field("위치",       "정문", width=100)
        src_tf  = make_field("소스 (0=웹캠 / rtsp://주소 / 파일경로)", expand=True)
        dir_dd  = ft.Dropdown(
            label="방향", width=110,
            options=[
                ft.dropdown.Option("ENTRY", "입차"),
                ft.dropdown.Option("EXIT",  "출차"),
                ft.dropdown.Option("BOTH",  "양방향"),
            ],
            value="BOTH",
            border_color=C_BORDER, focused_border_color=C_ACCENT,
            color=C_TEXT, label_style=ft.TextStyle(color=C_MUTED),
        )
        list_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=6)

        def refresh():
            list_col.controls = []
            for cam in get_all_cameras(conn):
                cam     = dict(cam)
                cid     = cam["camera_id"]
                running = cid in camera_threads

                def mk_toggle(c=cam):
                    def _toggle(e):
                        cid2 = c["camera_id"]
                        if cid2 in camera_threads:
                            _, se = camera_threads.pop(cid2)
                            se.set()
                            snack(f"{cid2} 중지됨", C_YELLOW)
                        else:
                            t, se = start_camera_thread(
                                c["source"] or "0", cid2,
                                c["direction"], on_detected=on_detected)
                            camera_threads[cid2] = (t, se)
                            snack(f"{cid2} 시작됨", C_GREEN)
                        refresh()
                        page.update()
                    return _toggle

                def mk_del(cid2=cid):
                    def _del(e):
                        if cid2 in camera_threads:
                            _, se = camera_threads.pop(cid2)
                            se.set()
                        delete_camera(conn, cid2)
                        snack(f"{cid2} 삭제됨", C_RED)
                        refresh()
                        page.update()
                    return _del

                list_col.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Text(cam["camera_id"], size=13, weight=ft.FontWeight.W_700,
                                color=C_TEXT, width=90),
                        ft.Text(cam["name"],      size=13, color=C_MUTED, width=120),
                        ft.Text(cam["location"],  size=13, color=C_MUTED, width=80),
                        ft.Text(cam["direction"], size=13, color=C_ACCENT, width=70),
                        ft.Text(cam["source"] or "-", size=12, color=C_MUTED, expand=True),
                        ft.Container(
                            content=ft.Text("실행 중" if running else "정지",
                                            size=11, color="white"),
                            bgcolor=C_GREEN if running else C_MUTED,
                            border_radius=5, padding=ft.padding.symmetric(3, 8),
                        ),
                        ft.ElevatedButton(
                            "중지" if running else "시작",
                            icon=ft.Icons.STOP if running else ft.Icons.PLAY_ARROW,
                            bgcolor=C_RED if running else C_GREEN,
                            color="white", on_click=mk_toggle(cam),
                        ),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=C_RED,
                                      on_click=mk_del(), tooltip="삭제"),
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor=C_SURFACE, border_radius=8,
                    padding=ft.padding.symmetric(10, 12),
                    border=ft.border.all(1, C_GREEN + "55" if running else C_BORDER),
                    margin=ft.margin.only(bottom=3),
                ))
            page.update()

        def do_add(e):
            if not cid_tf.value.strip():
                snack("카메라 ID를 입력하세요", C_RED)
                return
            upsert_camera(conn, cid_tf.value.strip(), name_tf.value,
                          loc_tf.value, dir_dd.value or "BOTH", src_tf.value)
            snack(f"{cid_tf.value} 등록 완료", C_GREEN)
            for tf in [cid_tf, name_tf, loc_tf, src_tf]:
                tf.value = ""
            refresh()
            page.update()

        refresh()
        return ft.Column([
            section_title("카메라 설정"),
            ft.Container(
                content=ft.Column([
                    ft.Text("카메라 추가", size=13, color=C_MUTED),
                    ft.Row([cid_tf, name_tf, loc_tf, dir_dd, src_tf,
                            ft.ElevatedButton("추가", icon=ft.Icons.ADD,
                                              on_click=do_add,
                                              bgcolor=C_ACCENT, color="white")],
                           spacing=8),
                ], spacing=10),
                bgcolor=C_SURFACE, border_radius=12,
                padding=ft.padding.all(14), border=ft.border.all(1, C_BORDER),
            ),
            ft.Row([
                ft.Text("ID",   size=12, color=C_MUTED, width=90),
                ft.Text("이름", size=12, color=C_MUTED, width=120),
                ft.Text("위치", size=12, color=C_MUTED, width=80),
                ft.Text("방향", size=12, color=C_MUTED, width=70),
                ft.Text("소스", size=12, color=C_MUTED, expand=True),
            ], spacing=8),
            list_col,
        ], spacing=12, expand=True)

    # ── 영상 처리 ─────────────────────────────────────────────
    def build_video():
        current_idx["v"] = 5
        path_tf   = make_field("영상 파일 경로", "C:\\영상\\parking.mp4", expand=True)
        cam_dd    = ft.Dropdown(
            label="카메라 ID", width=130,
            options=[ft.dropdown.Option(c["camera_id"]) for c in get_all_cameras(conn)],
            value="CAM_01",
            border_color=C_BORDER, focused_border_color=C_ACCENT,
            color=C_TEXT, label_style=ft.TextStyle(color=C_MUTED),
        )
        prog_bar  = ft.ProgressBar(value=0, bgcolor=C_SURF2, color=C_ACCENT, height=8)
        prog_text = ft.Text("대기 중", size=13, color=C_MUTED)
        start_btn = ft.ElevatedButton(
            "처리 시작", icon=ft.Icons.PLAY_ARROW,
            bgcolor=C_GREEN, color="white")
        stop_btn  = ft.ElevatedButton(
            "중단", icon=ft.Icons.STOP,
            bgcolor=C_RED, color="white", disabled=True)
        stop_ev   = threading.Event()

        # ── 영상 프리뷰 ──────────────────────────────────────
        preview_img = ft.Image(
            src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
            width=640, height=360,
            fit="contain",
            border_radius=8,
        )
        preview_box = ft.Container(
            content=ft.Stack([
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.VIDEOCAM_OFF_OUTLINED,
                                color=C_MUTED, size=48),
                        ft.Text("영상을 선택하고 처리를 시작하세요",
                                size=13, color=C_MUTED),
                    ], alignment=ft.MainAxisAlignment.CENTER,
                       horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    alignment=ft.Alignment.CENTER,  # 💡 ft.alignment.center 에서 수정
                    expand=True,
                ),
                preview_img,
            ]),
            bgcolor=C_SURF2, border_radius=8,
            border=ft.border.all(1, C_BORDER),
            height=360, expand=True,
            alignment=ft.Alignment.CENTER,  # 💡 ft.alignment.center 에서 수정
        )

        # 최근 인식 결과 (영상 처리 중 실시간)
        recent_col = ft.Column(
            scroll=ft.ScrollMode.AUTO, spacing=3, expand=True,
        )

        _preview_stop = threading.Event()

        def _preview_loop(video_path: str):
            """별도 스레드: 영상을 읽어 프리뷰 이미지 업데이트."""
            import time
            PREVIEW_INTERVAL = 0.1   # 100ms → 최대 초당 10프레임만 UI 반영
            cap = cv2.VideoCapture(video_path)
            next_update = 0.0
            try:
                while not _preview_stop.is_set():
                    ret, frame = cap.read()
                    if not ret:
                        break
                    now = time.time()
                    if now < next_update:
                        continue
                    next_update = now + PREVIEW_INTERVAL
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    b64    = base64.b64encode(buf).decode()
                    preview_img.src = f"data:image/jpeg;base64,{b64}"
                    try:
                        page.update()
                    except Exception:
                        break
            finally:
                cap.release()

        def on_detected_video(result: dict):
            """영상 처리 중 인식 결과 실시간 표시."""
            # 블랙리스트 경보는 별도 처리
            if result.get("blacklisted"):
                alert_list.insert(0, {
                    "msg":    f"차단 차량: {result['plate_text']}",
                    "ts":     result["timestamp"],
                    "camera": result["camera_id"],
                })
                trigger_alert(
                    plate_text=result["plate_text"],
                    camera_id=result["camera_id"],
                    timestamp=result["timestamp"],
                    on_popup=_show_blacklist_popup,
                )
            rd = {**result, "is_white": result.get("whitelisted", False),
                            "is_black": result.get("blacklisted", False)}
            recent_col.controls.insert(0, log_row(rd))
            if len(recent_col.controls) > 30:
                recent_col.controls = recent_col.controls[:30]
            try:
                page.update()
            except Exception:
                pass

        def on_progress(cur, total, detected):
            prog_bar.value  = cur / max(total, 1)
            prog_text.value = f"진행: {cur:,} / {total:,} 프레임 | 인식: {detected}건"
            try:
                page.update()
            except Exception:
                pass

        def do_start(e):
            path = path_tf.value.strip()
            if not path or not os.path.isfile(path):
                snack("유효한 파일 경로를 입력하세요", C_RED)
                return
            stop_ev.clear()
            _preview_stop.clear()
            recent_col.controls.clear()
            start_btn.disabled = True
            stop_btn.disabled  = False
            prog_bar.value     = 0
            prog_text.value    = "처리 시작..."
            page.update()

            # 프리뷰 스레드
            threading.Thread(
                target=_preview_loop, args=(path,), daemon=True
            ).start()

            # 인식 스레드
            def _run():
                count = process_video(
                    path,
                    camera_id   = cam_dd.value or "CAM_01",
                    stop_event  = stop_ev,
                    on_detected = on_detected_video,
                    on_progress = on_progress,
                )
                _preview_stop.set()
                start_btn.disabled = False
                stop_btn.disabled  = True
                prog_text.value    = f"완료 — 총 {count}건 인식"
                snack(f"처리 완료: {count}건", C_GREEN)
                try:
                    page.update()
                except Exception:
                    pass

            threading.Thread(target=_run, daemon=True).start()

        def do_stop(e):
            stop_ev.set()
            _preview_stop.set()
            prog_text.value    = "중단 요청됨..."
            stop_btn.disabled  = True
            start_btn.disabled = False
            page.update()

        start_btn.on_click = do_start
        stop_btn.on_click  = do_stop

        return ft.Column([
            section_title("영상 파일 처리"),
            ft.Container(
                content=ft.Column([
                    ft.Row([path_tf, cam_dd], spacing=8),
                    ft.Row([start_btn, stop_btn], spacing=10),
                    prog_text,
                    prog_bar,
                ], spacing=12),
                bgcolor=C_SURFACE, border_radius=12,
                padding=ft.padding.all(16), border=ft.border.all(1, C_BORDER),
            ),
            ft.Row([
                preview_box,
                ft.Container(
                    content=ft.Column([
                        ft.Text("실시간 인식 결과", size=13,
                                color=C_MUTED, weight=ft.FontWeight.W_600),
                        recent_col,
                    ], spacing=8, expand=True),
                    bgcolor=C_SURFACE, border_radius=8,
                    padding=ft.padding.all(12),
                    border=ft.border.all(1, C_BORDER),
                    width=420, expand=False,
                ),
            ], spacing=12, expand=True),
        ], spacing=16, expand=True)

    # ── 인식 설정 ─────────────────────────────────────────────
    def build_settings():
        import pipeline as pl
        current_idx["v"] = 6

        def num_field(label, val, width=130):
            return ft.TextField(
                label=label, value=str(val), width=width,
                border_color=C_BORDER, focused_border_color=C_ACCENT,
                color=C_TEXT, label_style=ft.TextStyle(color=C_MUTED),
                text_size=14, keyboard_type=ft.KeyboardType.NUMBER,
            )

        vote_f     = num_field("투표 프레임 수",     pl.VOTE_FRAMES)
        ratio_f    = num_field("확정 득표율 (0~1)",  pl.VOTE_WIN_RATIO)
        cooldown_f = num_field("쿨다운 (초)",        pl.COOLDOWN_SEC)
        every_f    = num_field("처리 주기 (N프레임)", pl.PROCESS_EVERY_N)
        iou_f      = num_field("Bbox IoU 임계값",    pl.VOTE_IOU_TH)
        fuzzy_f    = num_field("Fuzzy 편집거리",      pl.FUZZY_DIST)

        status_text = ft.Text("", size=13, color=C_GREEN)

        def do_apply(e):
            try:
                pl.VOTE_FRAMES     = max(1,   int(vote_f.value))
                pl.VOTE_WIN_RATIO  = max(0.1, min(1.0, float(ratio_f.value)))
                pl.COOLDOWN_SEC    = max(1,   int(cooldown_f.value))
                pl.PROCESS_EVERY_N = max(1,   int(every_f.value))
                pl.VOTE_IOU_TH     = max(0.1, min(0.9, float(iou_f.value)))
                pl.FUZZY_DIST      = max(0,   int(fuzzy_f.value))
                status_text.value  = "✅ 적용 완료 — 실행 중인 카메라에 즉시 반영됩니다"
                status_text.color  = C_GREEN
            except ValueError:
                status_text.value  = "❌ 숫자 형식을 확인하세요"
                status_text.color  = C_RED
            page.update()

        def do_reset(e):
            pl.VOTE_FRAMES = 7;    vote_f.value     = "7"
            pl.VOTE_WIN_RATIO = 0.4; ratio_f.value  = "0.4"
            pl.COOLDOWN_SEC = 30;  cooldown_f.value = "30"
            pl.PROCESS_EVERY_N = 3; every_f.value   = "3"
            pl.VOTE_IOU_TH = 0.3;  iou_f.value      = "0.3"
            pl.FUZZY_DIST = 2;     fuzzy_f.value     = "2"
            status_text.value = "↩ 기본값으로 초기화됨"
            status_text.color = C_MUTED
            page.update()

        def param_card(title, desc, field, recommend):
            return ft.Container(
                content=ft.Row([
                    ft.Column([
                        ft.Text(title, size=14, weight=ft.FontWeight.W_600, color=C_TEXT),
                        ft.Text(desc,  size=12, color=C_MUTED),
                        ft.Text(f"권장: {recommend}", size=11, color=C_ACCENT),
                    ], spacing=3, expand=True),
                    field,
                ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=C_SURFACE, border_radius=10,
                padding=ft.padding.all(16),
                border=ft.border.all(1, C_BORDER),
            )

        return ft.Column([
            section_title("인식 파라미터 설정"),
            ft.Container(
                content=ft.Text(
                    "투표(Voting): 같은 번호판을 여러 프레임에서 읽어 최다 득표 결과를 확정합니다.\n"
                    "Fuzzy 중복 제거: 편집거리 이내의 유사 번호판은 신뢰도 높은 것만 저장합니다.\n"
                    "예) 55가1234 / 555가1234 → 투표+Fuzzy로 55가1234 단 1건만 저장",
                    size=13, color="#CBD5E1",
                ),
                bgcolor=C_SURF2, border_radius=8,
                padding=ft.padding.all(12),
                border=ft.border.all(1, C_ACCENT + "66"),
            ),
            ft.Column([
                param_card("투표 프레임 수",
                           "이 프레임 수 모은 뒤 결과 확정. 클수록 안정적이나 느림.",
                           vote_f, "실내 7 / 고속도로 5"),
                param_card("확정 득표율",
                           "최다 득표 텍스트가 전체의 이 비율 이상이어야 확정.",
                           ratio_f, "0.4 (40%)"),
                param_card("쿨다운 (초)",
                           "같은 번호판 재저장 방지 시간. 길수록 중복 저장 감소.",
                           cooldown_f, "실내 30 / 입출차 60"),
                param_card("처리 주기",
                           "N 프레임마다 OCR 1회 실행. 작을수록 투표 샘플 풍부.",
                           every_f, "3"),
                param_card("Bbox IoU 임계값",
                           "이 값 이상 겹치는 박스를 '같은 번호판' 위치로 인식.",
                           iou_f, "0.3"),
                param_card("Fuzzy 편집거리",
                           "이 거리 이내 텍스트는 같은 차량으로 간주. 0=정확히 일치만.",
                           fuzzy_f, "2"),
            ], spacing=10),
            ft.Row([
                ft.ElevatedButton("적용", icon=ft.Icons.CHECK,
                                  on_click=do_apply, bgcolor=C_GREEN, color="white"),
                ft.OutlinedButton("기본값으로", icon=ft.Icons.REFRESH,
                                  on_click=do_reset),
                status_text,
            ], spacing=12),
        ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

    # ── 통계 차트 ─────────────────────────────────────────────
    def build_stats():
        current_idx["v"] = 7
        date_tf   = make_field("날짜", value=datetime.now().strftime("%Y-%m-%d"), width=160)
        chart_col = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

        def draw_bar(data: list, label_key: str, value_keys: list,
                     colors: list, title: str, y_label: str = "건수") -> ft.Container:
            """간단한 막대 차트 (Flet 네이티브)"""
            if not data:
                return ft.Container(
                    content=ft.Text("데이터 없음", size=13, color=C_MUTED),
                    bgcolor=C_SURFACE, border_radius=10,
                    padding=ft.padding.all(20),
                    border=ft.border.all(1, C_BORDER),
                )

            max_val = max(
                (sum(int(row[k] or 0) for k in value_keys)
                 for row in data), default=1
            )

            bars = []
            for row in data:
                row    = dict(row)
                label  = str(row.get(label_key, ""))
                vals   = [int(row.get(k) or 0) for k in value_keys]
                total  = sum(vals)
                segs   = []
                for v, c in zip(vals, colors):
                    h = int((v / max(max_val, 1)) * 80)
                    segs.append(ft.Container(
                        width=24, height=h,
                        bgcolor=c, border_radius=ft.BorderRadius(3, 3, 0, 0),
                        tooltip=f"{label}시 {v}건",
                    ))
                bars.append(ft.Column([
                    ft.Text(str(total), size=10, color=C_MUTED,
                            text_align=ft.TextAlign.CENTER, width=28),
                    ft.Row(segs, spacing=1),
                    ft.Text(label if len(label) <= 2 else label[-2:],
                            size=10, color=C_MUTED,
                            text_align=ft.TextAlign.CENTER, width=28),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER))

            legend = []
            for k, c in zip(value_keys, colors):
                legend.append(ft.Row([
                    ft.Container(width=12, height=12, bgcolor=c, border_radius=2),
                    ft.Text({"cnt": "인식", "entries": "입차", "exits": "출차"}.get(k, k),
                            size=11, color=C_MUTED),
                ], spacing=4))

            return ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text(title, size=14, weight=ft.FontWeight.W_600, color=C_TEXT),
                        ft.Container(expand=True),
                        *legend,
                    ], spacing=8),
                    ft.Container(
                        content=ft.Row(bars, spacing=4,
                                       alignment=ft.MainAxisAlignment.START,
                                       vertical_alignment=ft.CrossAxisAlignment.END),
                        height=120,
                    ),
                ], spacing=10),
                bgcolor=C_SURFACE, border_radius=10,
                padding=ft.padding.all(16),
                border=ft.border.all(1, C_BORDER),
            )

        def load_charts(e=None):
            d = date_tf.value.strip() or datetime.now().strftime("%Y-%m-%d")
            hourly     = query_hourly_stats(conn, d)
            ee_hourly  = query_entry_exit_hourly(conn, d)
            stats      = conn.execute("""
                SELECT plate_type, COUNT(*) AS cnt FROM plate_logs
                WHERE timestamp LIKE ? GROUP BY plate_type
            """, (f"{d}%",)).fetchall()

            chart_col.controls = [
                draw_bar(hourly, "hour", ["cnt"], [C_ACCENT],
                         f"{d} 시간대별 인식 건수"),
                draw_bar(ee_hourly, "hour", ["entries", "exits"],
                         [C_GREEN, C_RED],
                         f"{d} 시간대별 입출차"),
                ft.Container(
                    content=ft.Column([
                        ft.Text("번호판 타입 현황", size=14,
                                weight=ft.FontWeight.W_600, color=C_TEXT),
                        ft.Row([
                            ft.Column([
                                ft.Text(
                                    str(next((r["cnt"] for r in stats
                                              if r["plate_type"] == "NEW"), 0)),
                                    size=28, weight=ft.FontWeight.W_700,
                                    color=C_ACCENT),
                                ft.Text("신형", size=12, color=C_MUTED),
                            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                               expand=True),
                            ft.Container(width=1, height=60, bgcolor=C_BORDER),
                            ft.Column([
                                ft.Text(
                                    str(next((r["cnt"] for r in stats
                                              if r["plate_type"] == "OLD"), 0)),
                                    size=28, weight=ft.FontWeight.W_700,
                                    color=C_GREEN),
                                ft.Text("구형", size=12, color=C_MUTED),
                            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                               expand=True),
                        ], spacing=12),
                    ], spacing=12),
                    bgcolor=C_SURFACE, border_radius=10,
                    padding=ft.padding.all(16),
                    border=ft.border.all(1, C_BORDER),
                ),
            ]
            page.update()

        load_charts()
        return ft.Column([
            section_title("통계 차트"),
            ft.Row([
                date_tf,
                ft.ElevatedButton("조회", icon=ft.Icons.BAR_CHART,
                                  on_click=load_charts,
                                  bgcolor=C_ACCENT, color="white"),
            ], spacing=8),
            chart_col,
        ], spacing=14, expand=True)

    # ── 엑셀 내보내기 ─────────────────────────────────────────
    def build_export():
        current_idx["v"] = 8
        today = datetime.now().strftime("%Y-%m-%d")
        start_tf = make_field("시작 날짜", value=today, width=160)
        end_tf   = make_field("종료 날짜", value=today, width=160)
        status_t = ft.Text("", size=13, color=C_GREEN)

        def do_export_logs(e):
            try:
                rows = query_by_date_range(
                    conn, start_tf.value.strip(), end_tf.value.strip())
                if not rows:
                    status_t.value = "해당 기간에 데이터가 없습니다"
                    status_t.color = C_YELLOW
                else:
                    path = export_plate_logs(
                        rows, start_tf.value.strip(), end_tf.value.strip())
                    status_t.value = f"저장 완료: {path}"
                    status_t.color = C_GREEN
                    snack(f"엑셀 저장 완료 ({len(rows)}건)", C_GREEN)
            except Exception as ex:
                status_t.value = f"오류: {ex}"
                status_t.color = C_RED
            page.update()

        def do_export_ee(e):
            try:
                rows = query_entry_exit_range(
                    conn, start_tf.value.strip(), end_tf.value.strip())
                if not rows:
                    status_t.value = "해당 기간에 데이터가 없습니다"
                    status_t.color = C_YELLOW
                else:
                    path = export_entry_exit(
                        rows, start_tf.value.strip(), end_tf.value.strip())
                    status_t.value = f"저장 완료: {path}"
                    status_t.color = C_GREEN
                    snack(f"입출차 엑셀 저장 완료 ({len(rows)}건)", C_GREEN)
            except Exception as ex:
                status_t.value = f"오류: {ex}"
                status_t.color = C_RED
            page.update()

        return ft.Column([
            section_title("엑셀 내보내기"),
            ft.Container(
                content=ft.Column([
                    ft.Text("기간 선택", size=13, color=C_MUTED),
                    ft.Row([start_tf,
                            ft.Text("~", size=16, color=C_MUTED),
                            end_tf], spacing=8),
                    ft.Row([
                        ft.ElevatedButton(
                            "인식 기록 내보내기",
                            icon=ft.Icons.TABLE_CHART,
                            on_click=do_export_logs,
                            bgcolor=C_ACCENT, color="white"),
                        ft.ElevatedButton(
                            "입출차 기록 내보내기",
                            icon=ft.Icons.SWAP_VERT,
                            on_click=do_export_ee,
                            bgcolor=C_GREEN, color="white"),
                    ], spacing=10),
                    status_t,
                ], spacing=12),
                bgcolor=C_SURFACE, border_radius=12,
                padding=ft.padding.all(16),
                border=ft.border.all(1, C_BORDER),
            ),
            ft.Container(
                content=ft.Column([
                    ft.Text("저장 위치", size=13, color="#CBD5E1"),
                    ft.Text("lpr_system/exports/ 폴더",
                            size=13, color="white"),
                ], spacing=6),
                bgcolor=C_SURF2, border_radius=8,
                padding=ft.padding.all(12),
                border=ft.border.all(1, C_BORDER),
            ),
        ], spacing=14, expand=True)

    # ── 주차 요금 계산 ────────────────────────────────────────
    def build_parking_fee():
        current_idx["v"] = 9
        fee_cfg   = {**FEE_CONFIG}
        plate_tf  = make_field("번호판 검색", "예) 123가4567", width=200)
        result_col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

        # 요금 설정 필드
        free_tf  = make_field("무료 시간(분)",   value=str(fee_cfg["free_min"]),  width=110)
        base_f   = make_field("기본 요금(원)",   value=str(fee_cfg["base_fee"]),  width=110)
        basem_f  = make_field("기본 시간(분)",   value=str(fee_cfg["base_min"]),  width=110)
        unit_f   = make_field("추가 단위(원)",   value=str(fee_cfg["unit_fee"]),  width=110)
        unitm_f  = make_field("추가 시간(분)",   value=str(fee_cfg["unit_min"]),  width=110)
        max_f    = make_field("1일 최대(원)",    value=str(fee_cfg["daily_max"]), width=110)
        cfg_st   = ft.Text("", size=12, color=C_GREEN)

        def do_save_cfg(e):
            try:
                fee_cfg.update(
                    free_min  = int(free_tf.value),
                    base_fee  = int(base_f.value),
                    base_min  = int(basem_f.value),
                    unit_fee  = int(unit_f.value),
                    unit_min  = int(unitm_f.value),
                    daily_max = int(max_f.value),
                )
                cfg_st.value = "✅ 요금 설정 저장됨"
                cfg_st.color = C_GREEN
            except ValueError:
                cfg_st.value = "❌ 숫자 형식 확인"
                cfg_st.color = C_RED
            page.update()

        def do_calc(e=None):
            plate = plate_tf.value.strip()
            result_col.controls = []

            # 현재 주차 중인 차량 요금 표시
            parked = get_current_parked(conn)
            if plate:
                parked = [p for p in parked
                          if plate.lower() in dict(p)["plate_text"].lower()]

            if not parked:
                result_col.controls.append(
                    ft.Text("현재 주차 중인 차량이 없습니다", size=13, color=C_MUTED))
                page.update()
                return

            for p in parked:
                p = dict(p)
                info = calc_fee(p["entry_time"], config=fee_cfg)

                color = C_GREEN if info["is_free"] else C_ACCENT
                result_col.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(p["plate_text"], size=16,
                                    weight=ft.FontWeight.W_700, color=C_TEXT),
                            ft.Text(p.get("owner_name") or "미등록",
                                    size=12, color=C_MUTED),
                        ], spacing=2, expand=True),
                        ft.Column([
                            ft.Text(info["duration_str"], size=14, color=C_MUTED),
                            ft.Text(info["fee_str"], size=20,
                                    weight=ft.FontWeight.W_700, color=color),
                            ft.Text("무료" if info["is_free"] else "",
                                    size=11, color=C_GREEN),
                        ], spacing=2,
                           horizontal_alignment=ft.CrossAxisAlignment.END),
                    ], spacing=12,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    bgcolor=C_SURFACE, border_radius=10,
                    padding=ft.padding.all(16),
                    border=ft.border.all(1, C_BORDER),
                    margin=ft.margin.only(bottom=4),
                ))

            page.update()

        plate_tf.on_submit = do_calc
        do_calc()

        return ft.Column([
            section_title("주차 요금 계산"),
            ft.Row([
                ft.Container(
                    content=ft.Column([
                        ft.Text("요금 설정", size=13, color=C_MUTED),
                        ft.Row([free_tf, base_f, basem_f], spacing=8),
                        ft.Row([unit_f, unitm_f, max_f], spacing=8),
                        ft.Row([
                            ft.ElevatedButton("저장", on_click=do_save_cfg,
                                              bgcolor=C_ACCENT, color="white",
                                              icon=ft.Icons.SAVE),
                            cfg_st,
                        ], spacing=8),
                    ], spacing=10),
                    bgcolor=C_SURFACE, border_radius=12,
                    padding=ft.padding.all(14),
                    border=ft.border.all(1, C_BORDER),
                    expand=True,
                ),
            ]),
            ft.Row([
                plate_tf,
                ft.ElevatedButton("조회", icon=ft.Icons.CALCULATE,
                                  on_click=do_calc,
                                  bgcolor=C_ACCENT, color="white"),
            ], spacing=8),
            ft.Text("현재 주차 중인 차량 요금", size=13, color=C_MUTED),
            result_col,
        ], spacing=14, expand=True)

    # ── 네비게이션 ─────────────────────────────────────────────
    builders = [
        build_dashboard, build_search, build_whitelist,
        build_blacklist, build_cameras, build_video,
        build_settings, build_stats, build_export, build_parking_fee,
    ]

    sidebar_col = ft.Column(spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    def update_sidebar():
        menu_items = [
            (ft.Icons.DASHBOARD, "대시보드"), (ft.Icons.SEARCH, "조회"),
            (ft.Icons.CHECK_CIRCLE, "화이트"), (ft.Icons.BLOCK, "블랙"),
            (ft.Icons.VIDEOCAM, "카메라"), (ft.Icons.VIDEO_FILE, "영상처리"),
            (ft.Icons.TUNE, "인식설정"), (ft.Icons.BAR_CHART, "통계"),
            (ft.Icons.TABLE_CHART, "엑셀"), (ft.Icons.CALCULATE, "요금"),
        ]
        
        sidebar_col.controls = []
        for i, (icon, label) in enumerate(menu_items):
            is_selected = (current_idx["v"] == i)
            
            # 💡 여기서 글자와 아이콘 색상을 직접 바꿀 수 있습니다!
            # is_selected 가 True면 선택된 색, False면 선택 안 된 색입니다.
            text_icon_color = "#FFFFFF" if is_selected else "#94A3B8"  
            
            # 💡 여기서 메뉴 버튼의 배경색을 바꿀 수 있습니다!
            menu_bg_color = "#4450FF" if is_selected else None         
            
            sidebar_col.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(icon, color=text_icon_color, size=22),
                        ft.Text(label, color=text_icon_color, size=11, 
                                weight=ft.FontWeight.W_600 if is_selected else None),
                    ], spacing=4, alignment=ft.MainAxisAlignment.CENTER, 
                       horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    width=74, height=62, border_radius=8,
                    bgcolor=menu_bg_color,
                    on_click=lambda e, idx=i: nav_change_custom(idx)
                )
            )

    def nav_change_custom(idx):
        current_idx["v"] = idx
        content_ref.current.controls = [builders[idx]()]
        update_sidebar()  
        page.update()

    nav_container = ft.Container(
        content=sidebar_col,
        bgcolor="#100a6d", # 💡 전체 사이드바 배경색
        padding=ft.padding.symmetric(vertical=12, horizontal=4),
        width=82,
    )

    # ── 헤더 및 화면 출력 ───────────────────────────────────────
    header = ft.Container(
        content=ft.Row([

            ft.Text("한국 번호판 인식 시스템", size=17,
                    weight=ft.FontWeight.W_700, color=C_TEXT),
            ft.Container(expand=True),
            ft.Text(datetime.now().strftime("%Y-%m-%d %H:%M"),
                    size=12, color=C_MUTED, ref=header_time_ref),
        ], spacing=10),
        bgcolor=C_SURFACE,
        padding=ft.padding.symmetric(13, 20),
        border=ft.border.only(bottom=ft.BorderSide(1, C_BORDER)),
    )

    content = ft.Column(
        ref=content_ref,
        controls=[build_dashboard()],
        expand=True,
    )

    page.add(ft.Column([
        header,
        ft.Row([
            nav_container, 
            ft.VerticalDivider(width=1, color=C_BORDER),
            ft.Container(content=content, expand=True, padding=ft.padding.all(18)),
        ], expand=True, spacing=0),
    ], expand=True, spacing=0))

    update_sidebar() 
    _refresh_dashboard()

    def on_close(e):
        _stop_tick.set()
        for _, (_, se) in list(camera_threads.items()):
            try:
                se.set()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass

    page.on_close = on_close

if __name__ == "__main__":
    ft.app(target=main, assets_dir="assets")