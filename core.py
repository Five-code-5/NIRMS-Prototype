
import os
import sqlite3
from datetime import date, datetime, timedelta

from flask import g

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
   if os.environ.get("VERCEL"):
       import shutil
       DB_PATH = "/tmp/nirms.db"
       if not os.path.exists(DB_PATH):
           shutil.copy(os.path.join(BASE_DIR, "nirms.db"), DB_PATH)
   else:
       DB_PATH = os.path.join(BASE_DIR, "nirms.db")

CATEGORIES = ["Internet Down", "Slow Connection", "WiFi Issue",
              "Website / Server Down", "Network Equipment", "Other"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
STATUSES = ["Open", "In Progress", "Resolved", "Closed"]


def get_db():
    """Open the database once per web request and reuse it."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row          # lets us write row["email"]
        g.db.execute("PRAGMA foreign_keys = ON")
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
