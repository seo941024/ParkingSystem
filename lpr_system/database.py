"""
database.py — SQLite DB
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parking.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-8000")   # 8MB 캐시
    return conn


def init_db() -> None:
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS plate_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_text   TEXT    NOT NULL,
            plate_color  TEXT    NOT NULL,
            plate_type   TEXT    NOT NULL,
            confidence   REAL    DEFAULT 0,
            timestamp    TEXT    NOT NULL,
            camera_id    TEXT    DEFAULT 'CAM_01',
            image_path   TEXT    DEFAULT '',
            frame_number INTEGER DEFAULT 0,
            source_file  TEXT    DEFAULT ''
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS whitelist (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_text    TEXT    NOT NULL UNIQUE,
            owner_name    TEXT    DEFAULT '',
            vehicle_desc  TEXT    DEFAULT '',
            phone         TEXT    DEFAULT '',
            memo          TEXT    DEFAULT '',
            registered_at TEXT    NOT NULL,
            expires_at    TEXT    DEFAULT ''
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_text    TEXT    NOT NULL UNIQUE,
            reason        TEXT    DEFAULT '',
            registered_at TEXT    NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS entry_exit_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_text   TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            camera_id    TEXT DEFAULT '',
            timestamp    TEXT NOT NULL,
            image_path   TEXT DEFAULT ''
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS camera_config (
            camera_id  TEXT PRIMARY KEY,
            name       TEXT DEFAULT '',
            location   TEXT DEFAULT '',
            direction  TEXT DEFAULT 'BOTH',
            source     TEXT DEFAULT '',
            enabled    INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_pl_plate ON plate_logs (plate_text)",
        "CREATE INDEX IF NOT EXISTS idx_pl_ts    ON plate_logs (timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_pl_cam   ON plate_logs (camera_id)",
        "CREATE INDEX IF NOT EXISTS idx_ee_plate ON entry_exit_log (plate_text)",
        "CREATE INDEX IF NOT EXISTS idx_ee_ts    ON entry_exit_log (timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_ee_type  ON entry_exit_log (event_type)",
    ]:
        c.execute(sql)

    c.execute("""
        INSERT OR IGNORE INTO camera_config
          (camera_id, name, location, direction, source, created_at)
        VALUES ('CAM_01','정문 카메라','정문','BOTH','0',?)
    """, (datetime.now().isoformat(),))

    conn.commit()
    conn.close()


# ── plate_logs ────────────────────────────────────────────────

def insert_plate_log(conn, plate_text, plate_color, plate_type,
                     confidence, timestamp, camera_id="CAM_01",
                     image_path="", frame_number=0, source_file="") -> int:
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO plate_logs
              (plate_text,plate_color,plate_type,confidence,
               timestamp,camera_id,image_path,frame_number,source_file)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (plate_text, plate_color, plate_type, confidence,
              timestamp, camera_id, image_path, frame_number, source_file))
        conn.commit()
        return c.lastrowid
    except sqlite3.Error:
        conn.rollback()
        return -1


def delete_plate_log(conn, log_id: int) -> bool:
    """plate_logs 단건 삭제 (오인식 제거용)."""
    try:
        conn.execute("DELETE FROM plate_logs WHERE id=?", (log_id,))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False


def delete_plate_log_by_text(conn, plate_text: str) -> int:
    """특정 번호판 텍스트의 plate_logs 전체 삭제. 삭제된 행 수 반환."""
    try:
        c = conn.execute("DELETE FROM plate_logs WHERE plate_text=?", (plate_text,))
        conn.commit()
        return c.rowcount
    except sqlite3.Error:
        conn.rollback()
        return 0


def query_all(conn, limit=50, offset=0):
    c = conn.cursor()
    c.execute("""
        SELECT pl.*,
               CASE WHEN w.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_white,
               CASE WHEN b.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_black
        FROM plate_logs pl
        LEFT JOIN whitelist w ON pl.plate_text = w.plate_text
        LEFT JOIN blacklist b ON pl.plate_text = b.plate_text
        ORDER BY pl.id DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    return c.fetchall()


def query_by_plate(conn, plate_text: str):
    c = conn.cursor()
    c.execute("""
        SELECT pl.*,
               CASE WHEN w.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_white,
               CASE WHEN b.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_black
        FROM plate_logs pl
        LEFT JOIN whitelist w ON pl.plate_text = w.plate_text
        LEFT JOIN blacklist b ON pl.plate_text = b.plate_text
        WHERE pl.plate_text LIKE ?
        ORDER BY pl.id DESC
        LIMIT 200
    """, (f"%{plate_text}%",))
    return c.fetchall()


def query_by_date(conn, date_str: str):
    c = conn.cursor()
    c.execute("""
        SELECT pl.*,
               CASE WHEN w.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_white,
               CASE WHEN b.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_black
        FROM plate_logs pl
        LEFT JOIN whitelist w ON pl.plate_text = w.plate_text
        LEFT JOIN blacklist b ON pl.plate_text = b.plate_text
        WHERE pl.timestamp LIKE ?
        ORDER BY pl.id DESC
        LIMIT 500
    """, (f"{date_str}%",))
    return c.fetchall()


def query_total_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM plate_logs").fetchone()[0]


def query_today_count(conn) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    return conn.execute(
        "SELECT COUNT(*) FROM plate_logs WHERE timestamp LIKE ?",
        (f"{today}%",)
    ).fetchone()[0]




# ── whitelist ─────────────────────────────────────────────────

def is_whitelisted(conn, plate_text: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM whitelist WHERE plate_text=?", (plate_text,)
    ).fetchone() is not None


def add_whitelist(conn, plate_text, owner_name="", vehicle_desc="",
                  phone="", memo="", expires_at="") -> bool:
    try:
        if is_whitelisted(conn, plate_text):
            return False
        conn.execute("""
            INSERT INTO whitelist
              (plate_text,owner_name,vehicle_desc,phone,memo,registered_at,expires_at)
            VALUES (?,?,?,?,?,?,?)
        """, (plate_text, owner_name, vehicle_desc, phone, memo,
              datetime.now().isoformat(), expires_at))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False



def delete_whitelist(conn, plate_text) -> bool:
    try:
        conn.execute("DELETE FROM whitelist WHERE plate_text=?", (plate_text,))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False


def get_all_whitelist(conn):
    return conn.execute(
        "SELECT * FROM whitelist ORDER BY registered_at DESC"
    ).fetchall()


# ── blacklist ─────────────────────────────────────────────────

def is_blacklisted(conn, plate_text: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM blacklist WHERE plate_text=?", (plate_text,)
    ).fetchone() is not None


def add_blacklist(conn, plate_text, reason="") -> bool:
    try:
        if is_blacklisted(conn, plate_text):
            return False
        conn.execute("""
            INSERT INTO blacklist (plate_text, reason, registered_at)
            VALUES (?,?,?)
        """, (plate_text, reason, datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False


def delete_blacklist(conn, plate_text) -> bool:
    try:
        conn.execute("DELETE FROM blacklist WHERE plate_text=?", (plate_text,))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False


def get_all_blacklist(conn):
    return conn.execute(
        "SELECT * FROM blacklist ORDER BY registered_at DESC"
    ).fetchall()


# ── entry_exit_log ────────────────────────────────────────────

def insert_entry_exit(conn, plate_text, event_type,
                      camera_id="", image_path="") -> int:
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO entry_exit_log
              (plate_text, event_type, camera_id, timestamp, image_path)
            VALUES (?,?,?,?,?)
        """, (plate_text, event_type, camera_id,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S"), image_path))
        conn.commit()
        return c.lastrowid
    except sqlite3.Error:
        conn.rollback()
        return -1



def delete_entry_exit_by_text(conn, plate_text: str) -> int:
    """특정 번호판의 입출차 기록 전체 삭제. 삭제된 행 수 반환."""
    try:
        c = conn.execute(
            "DELETE FROM entry_exit_log WHERE plate_text=?", (plate_text,)
        )
        conn.commit()
        return c.rowcount
    except sqlite3.Error:
        conn.rollback()
        return 0


def manual_exit(conn, plate_text: str, camera_id: str = "") -> bool:
    """수동 출차 처리 — 현재 주차 중인 차량에 EXIT 이벤트 삽입."""
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO entry_exit_log
              (plate_text, event_type, camera_id, timestamp, image_path)
            VALUES (?, 'EXIT', ?, ?, '')
        """, (plate_text, camera_id,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False


def get_current_parked(conn):
    """
    현재 주차 중인 차량.
    마지막 이벤트가 ENTRY인 번호판 = 주차 중.
    """
    c = conn.cursor()
    c.execute("""
        SELECT e.plate_text,
               e.timestamp  AS entry_time,
               e.camera_id,
               w.owner_name,
               w.vehicle_desc
        FROM entry_exit_log e
        JOIN (
            SELECT plate_text, MAX(id) AS max_id
            FROM entry_exit_log
            GROUP BY plate_text
        ) latest ON e.id = latest.max_id
        LEFT JOIN whitelist w ON e.plate_text = w.plate_text
        WHERE e.event_type = 'ENTRY'
        ORDER BY e.timestamp DESC
    """)
    return c.fetchall()


# ── camera_config ─────────────────────────────────────────────

def get_all_cameras(conn):
    return conn.execute(
        "SELECT * FROM camera_config ORDER BY camera_id"
    ).fetchall()


def upsert_camera(conn, camera_id, name="", location="",
                  direction="BOTH", source="", enabled=1) -> bool:
    try:
        conn.execute("""
            INSERT INTO camera_config
              (camera_id, name, location, direction, source, enabled, created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(camera_id) DO UPDATE SET
              name=excluded.name,
              location=excluded.location,
              direction=excluded.direction,
              source=excluded.source,
              enabled=excluded.enabled
        """, (camera_id, name, location, direction, source, enabled,
              datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False


def delete_camera(conn, camera_id) -> bool:
    try:
        conn.execute("DELETE FROM camera_config WHERE camera_id=?", (camera_id,))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False

# ── 엑셀 내보내기용 쿼리 ──────────────────────────────────────

def query_by_date_range(conn, start_date: str, end_date: str):
    """기간별 plate_logs 조회 (엑셀 내보내기용)"""
    c = conn.cursor()
    c.execute("""
        SELECT pl.*,
               CASE WHEN w.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_white,
               CASE WHEN b.plate_text IS NOT NULL THEN 1 ELSE 0 END AS is_black
        FROM plate_logs pl
        LEFT JOIN whitelist w ON pl.plate_text = w.plate_text
        LEFT JOIN blacklist b ON pl.plate_text = b.plate_text
        WHERE pl.timestamp BETWEEN ? AND ?
        ORDER BY pl.id DESC
    """, (f"{start_date} 00:00:00", f"{end_date} 23:59:59"))
    return c.fetchall()


def query_entry_exit_range(conn, start_date: str, end_date: str):
    """기간별 입출차 로그 조회"""
    c = conn.cursor()
    c.execute("""
        SELECT e.*,
               w.owner_name,
               w.vehicle_desc,
               w.phone
        FROM entry_exit_log e
        LEFT JOIN whitelist w ON e.plate_text = w.plate_text
        WHERE e.timestamp BETWEEN ? AND ?
        ORDER BY e.id DESC
    """, (f"{start_date} 00:00:00", f"{end_date} 23:59:59"))
    return c.fetchall()


def query_hourly_stats(conn, date_str: str = None):
    """시간대별 인식 건수 (통계 차트용)"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    c = conn.cursor()
    c.execute("""
        SELECT strftime('%H', timestamp) AS hour, COUNT(*) AS cnt
        FROM plate_logs
        WHERE timestamp LIKE ?
        GROUP BY hour
        ORDER BY hour
    """, (f"{date_str}%",))
    return c.fetchall()


def query_entry_exit_hourly(conn, date_str: str = None):
    """시간대별 입출차 건수 (통계 차트용)"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    c = conn.cursor()
    c.execute("""
        SELECT strftime('%H', timestamp) AS hour,
               SUM(CASE WHEN event_type='ENTRY' THEN 1 ELSE 0 END) AS entries,
               SUM(CASE WHEN event_type='EXIT'  THEN 1 ELSE 0 END) AS exits
        FROM entry_exit_log
        WHERE timestamp LIKE ?
        GROUP BY hour
        ORDER BY hour
    """, (f"{date_str}%",))
    return c.fetchall()


def query_parking_duration(conn, plate_text: str):
    """특정 번호판의 주차 시간 계산 (분 단위)"""
    c = conn.cursor()
    c.execute("""
        SELECT entry.timestamp AS entry_time,
               exit_.timestamp AS exit_time,
               CAST(
                 (julianday(exit_.timestamp) - julianday(entry.timestamp)) * 24 * 60
               AS INTEGER) AS duration_min
        FROM entry_exit_log entry
        JOIN entry_exit_log exit_
          ON entry.plate_text = exit_.plate_text
         AND exit_.event_type = 'EXIT'
         AND exit_.timestamp > entry.timestamp
        WHERE entry.plate_text = ?
          AND entry.event_type = 'ENTRY'
        ORDER BY entry.timestamp DESC
        LIMIT 20
    """, (plate_text,))
    return c.fetchall()
