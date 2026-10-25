
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

from flask import g

# Always use Philippine time (Vercel servers run in UTC)
os.environ.setdefault("TZ", "Asia/Manila")
if hasattr(time, "tzset"):
    time.tzset()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "nirms.db")          # used only when DATABASE_URL is not set
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extensions
    import psycopg2.extras
    IntegrityError = (psycopg2.IntegrityError, sqlite3.IntegrityError)
    # Postgres returns averages as Decimal; the app expects plain numbers
    psycopg2.extensions.register_type(psycopg2.extensions.new_type(
        psycopg2.extensions.DECIMAL.values, "DEC2FLOAT",
        lambda value, cur: float(value) if value is not None else None))
else:
    IntegrityError = sqlite3.IntegrityError

CATEGORIES = ["Internet Down", "Slow Connection", "WiFi Issue",
              "Website / Server Down", "Network Equipment", "Other"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
STATUSES = ["Open", "In Progress", "Resolved", "Closed"]


# --- Postgres support: lets the existing SQLite-style queries run on Postgres unchanged ---
_NOW_DATE = "to_char(now() AT TIME ZONE 'Asia/Manila', 'YYYY-MM-DD')"
_NOW_DATETIME = "to_char(now() AT TIME ZONE 'Asia/Manila', 'YYYY-MM-DD HH24:MI:SS')"
_NOW_EPOCH = "extract(epoch from (now() AT TIME ZONE 'Asia/Manila'))"


def _to_postgres(sql, has_params=True):
    """Rewrite the few SQLite-only bits of a query so Postgres understands it."""
    if has_params:
        sql = sql.replace("%", "%%")                              # literal % must be doubled
    sql = re.sub(r"datetime\(\s*'now'\s*,\s*'localtime'\s*\)", _NOW_DATETIME, sql)
    sql = re.sub(r"date\(\s*'now'\s*,\s*'localtime'\s*\)", _NOW_DATE, sql)
    sql = re.sub(r"julianday\(\s*'now'\s*,\s*'localtime'\s*\)", f"({_NOW_EPOCH} / 86400.0)", sql)
    sql = re.sub(r"julianday\(\s*([\w.]+)\s*\)",
                 r"(extract(epoch from NULLIF(\1, '')::timestamp) / 86400.0)", sql)
    sql = re.sub(r"\bdate\(\s*([\w.]+)\s*\)", r"NULLIF(substr(\1, 1, 10), '')", sql)
    sql = re.sub(r"\bLIKE\b", "ILIKE", sql)                        # SQLite LIKE ignores case
    sql = sql.replace("?", "%s")
    if sql.lstrip().upper().startswith("INSERT") and "RETURNING" not in sql.upper():
        sql = sql.rstrip().rstrip(";") + " RETURNING id"
    return sql


class _Result:
    """What db.execute() returns on Postgres: fetchone / fetchall / iteration / lastrowid."""

    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())


class _PgConnection:
    """Small wrapper that gives a Postgres connection the same db.execute(...) style as sqlite3."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        is_insert = sql.lstrip().upper().startswith("INSERT")
        try:
            cur.execute(_to_postgres(sql, bool(params)), tuple(params))
            last = cur.fetchone()[0] if is_insert and cur.description else None
        except Exception:
            self._conn.rollback()          # keep the connection usable after an error
            raise
        return _Result(cur, last)

    def executescript(self, script):
        cur = self._conn.cursor()
        cur.execute(script)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect():
    """Open a new database connection (Postgres if DATABASE_URL is set, otherwise SQLite)."""
    if USE_PG:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=15,
                                cursor_factory=psycopg2.extras.DictCursor)
        return _PgConnection(conn)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row              # lets us write row["email"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Open the database once per web request and reuse it."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    """Close the database when the request ends."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def load_user(session, role):
    """Return the logged-in user, but only if they have the role this app serves."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return get_db().execute(
        "SELECT * FROM users WHERE id = ? AND role = ?", (user_id, role)
    ).fetchone()

def next_ticket_no(db):
    """Make the next ticket number, e.g. NIRMS-2026-0001."""
    year = datetime.now().year
    count = db.execute(
        "SELECT COUNT(*) AS n FROM reports WHERE ticket_no LIKE ?", (f"NIRMS-{year}-%",)
    ).fetchone()["n"]
    return f"NIRMS-{year}-{count + 1:04d}"

DEVICE_TYPES = ["Not applicable", "Laptop", "Desktop", "Printer", "Router / Switch",
                "Access Point / WiFi", "Starlink / Fiber link", "Other"]
REPAIR_TYPES = ["", "In-house", "External (contractor / supplier)", "Needs procurement"]
TEST_RESULTS = ["Not done", "Passed", "Failed"]

# Days allowed to resolve a report, by priority. The NOC works to a 3-5 day target and
# ARTA uses 3 / 7 / 20 days. Change these numbers to match your office policy.
DUE_DAYS = {"Critical": 3, "High": 3, "Medium": 5, "Low": 7}

# SQL condition for "still open and past its due date" (use with the alias  r  for reports)
OVERDUE_SQL = ("r.status IN ('Open', 'In Progress') AND r.due_date != '' "
               "AND r.due_date < date('now', 'localtime')")


def default_due_date(priority):
    """Due date = today + the allowed days for this priority, as YYYY-MM-DD."""
    return (date.today() + timedelta(days=DUE_DAYS.get(priority, 5))).isoformat()

# ---------------- Part 3: security helpers ----------------
import hmac
import secrets
import time

from flask import abort, request


def get_secret_key(env_name, file_name):
    """Secret key for session cookies. Uses the environment variable if set; otherwise makes a
    random key once and keeps it in a small file next to the app (so logins survive restarts)."""
    key = os.environ.get(env_name)
    if key:
        return key
    path = os.path.join(BASE_DIR, file_name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def init_security(app):
    """Adds CSRF protection to an app: every POST form must carry the hidden csrf_token field."""
    from flask import session

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(sent, expected):
                abort(400, "Form expired or invalid. Go back, refresh the page and try again.")


# Simple login lock: 5 wrong passwords -> locked for 5 minutes (kept in memory)
_FAILS = {}
MAX_FAILS, LOCK_SECONDS = 5, 300


def login_locked(key):
    count, first = _FAILS.get(key, (0, 0))
    if count >= MAX_FAILS and time.time() - first < LOCK_SECONDS:
        return True
    if count >= MAX_FAILS:
        _FAILS.pop(key, None)
    return False


def login_failed(key):
    count, first = _FAILS.get(key, (0, time.time()))
    _FAILS[key] = (count + 1, first)


def login_ok(key):
    _FAILS.pop(key, None)
