"""
Database layer — PostgreSQL via psycopg2.

On startup, creates tables if they don't exist.
All JSON file storage is replaced by this module.

Railway automatically sets DATABASE_URL when you add a Postgres plugin.
"""

import os
import json
import logging
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS courses (
                    course_id     TEXT PRIMARY KEY,
                    schedule_id   TEXT NOT NULL,
                    booking_class TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    url           TEXT NOT NULL,
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id                   TEXT PRIMARY KEY,
                    course_id            TEXT NOT NULL,
                    course_name          TEXT,
                    schedule_id          TEXT NOT NULL,
                    booking_class        TEXT,
                    course_url           TEXT,
                    target_date          TEXT NOT NULL,
                    time_from            TEXT NOT NULL,
                    time_to              TEXT NOT NULL,
                    players              INTEGER NOT NULL,
                    holes                INTEGER DEFAULT 18,
                    platform             TEXT DEFAULT 'foreup',
                    status               TEXT DEFAULT 'polling',
                    available_times      JSONB DEFAULT '[]',
                    booked_confirmation  JSONB,
                    notification_sent    BOOLEAN DEFAULT FALSE,
                    last_polled          TIMESTAMPTZ,
                    created_at           TIMESTAMPTZ DEFAULT NOW(),
                    logs                 JSONB DEFAULT '[]'
                )
            """)
    logger.info("Database initialized")


# ── Config ────────────────────────────────────────────────────────────────────

DEFAULTS = {
    "email": "",
    "password": "",
    "default_players": "2",
    "default_holes": "18",
    "poll_interval": "120",
    "pushover_user_token": "",
    "pushover_app_token": "",
    "dashboard_url": "",
}

ENV_MAP = {
    "FOREUP_EMAIL":        "email",
    "FOREUP_PASSWORD":     "password",
    "PUSHOVER_USER_TOKEN": "pushover_user_token",
    "PUSHOVER_APP_TOKEN":  "pushover_app_token",
    "DASHBOARD_URL":       "dashboard_url",
    "POLL_INTERVAL":       "poll_interval",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM config")
                for key, value in cur.fetchall():
                    cfg[key] = value
    except Exception as e:
        logger.warning(f"Could not load config from DB: {e}")
    # Env vars always win
    for env_key, cfg_key in ENV_MAP.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            cfg[cfg_key] = val
    return cfg


def save_config(data: dict):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for key, value in data.items():
                    if value:
                        cur.execute("""
                            INSERT INTO config (key, value)
                            VALUES (%s, %s)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """, (key, value))
    except Exception as e:
        logger.error(f"Could not save config: {e}")


def credentials_from_env() -> bool:
    return bool(os.environ.get("FOREUP_EMAIL") and os.environ.get("FOREUP_PASSWORD"))


# ── Courses ───────────────────────────────────────────────────────────────────

def load_courses() -> dict:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM courses ORDER BY name")
                return {row["course_id"]: dict(row) for row in cur.fetchall()}
    except Exception as e:
        logger.error(f"Could not load courses: {e}")
        return {}


def save_course(course_id: str, info: dict):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO courses (course_id, schedule_id, booking_class, name, url)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (course_id) DO UPDATE SET
                        schedule_id   = EXCLUDED.schedule_id,
                        booking_class = EXCLUDED.booking_class,
                        name          = EXCLUDED.name,
                        url           = EXCLUDED.url
                """, (
                    course_id,
                    info["schedule_id"],
                    info["booking_class"],
                    info["name"],
                    info["url"],
                ))
        logger.info(f"Saved course {course_id}: {info['name']}")
    except Exception as e:
        logger.error(f"Could not save course: {e}")


def delete_course(course_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM courses WHERE course_id = %s", (course_id,))


# ── Jobs ──────────────────────────────────────────────────────────────────────

def load_all_jobs() -> list[dict]:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM jobs ORDER BY created_at DESC")
                rows = cur.fetchall()
                return [_deserialize_job(dict(r)) for r in rows]
    except Exception as e:
        logger.error(f"Could not load jobs: {e}")
        return []


def load_job(job_id: str) -> dict | None:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
                row = cur.fetchone()
                return _deserialize_job(dict(row)) if row else None
    except Exception as e:
        logger.error(f"Could not load job {job_id}: {e}")
        return None


def insert_job(job: dict):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO jobs (
                    id, course_id, course_name, schedule_id, booking_class,
                    course_url, target_date, time_from, time_to,
                    players, holes, status, platform, logs
                ) VALUES (
                    %(id)s, %(course_id)s, %(course_name)s, %(schedule_id)s, %(booking_class)s,
                    %(course_url)s, %(target_date)s, %(time_from)s, %(time_to)s,
                    %(players)s, %(holes)s, %(status)s, %(platform)s, %(logs)s
                )
            """, {**job, "platform": job.get("platform", "foreup"), "logs": json.dumps(job.get("logs", []))})


def update_job_fields(job_id: str, fields: dict):
    """Update arbitrary fields on a job row."""
    if not fields:
        return
    # Serialize any list/dict fields to JSON
    serialized = {}
    for k, v in fields.items():
        if isinstance(v, (list, dict)):
            serialized[k] = json.dumps(v)
        else:
            serialized[k] = v

    set_clause = ", ".join(f"{k} = %({k})s" for k in serialized)
    serialized["job_id"] = job_id
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE jobs SET {set_clause} WHERE id = %(job_id)s",
                    serialized
                )
    except Exception as e:
        logger.error(f"Could not update job {job_id}: {e}")


def delete_job(job_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def append_job_log(job_id: str, entry: str, max_logs: int = 100):
    """Append a log entry, keeping only the last max_logs entries."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE jobs
                    SET logs = (
                        SELECT jsonb_agg(e)
                        FROM (
                            SELECT e FROM jsonb_array_elements(logs) AS e
                            UNION ALL
                            SELECT %s::jsonb
                            ORDER BY 1
                            LIMIT %s
                        ) sub(e)
                    )
                    WHERE id = %s
                """, (json.dumps(entry), max_logs, job_id))
    except Exception as e:
        logger.error(f"Could not append log for job {job_id}: {e}")


def _deserialize_job(row: dict) -> dict:
    """Convert DB row types to plain Python dicts."""
    for field in ("available_times", "booked_confirmation", "logs"):
        val = row.get(field)
        if isinstance(val, str):
            try:
                row[field] = json.loads(val)
            except Exception:
                row[field] = [] if field != "booked_confirmation" else None
        elif val is None and field != "booked_confirmation":
            row[field] = []
    # Convert timestamps to ISO strings
    for field in ("last_polled", "created_at"):
        val = row.get(field)
        if val and hasattr(val, "isoformat"):
            row[field] = val.isoformat()
    return row
