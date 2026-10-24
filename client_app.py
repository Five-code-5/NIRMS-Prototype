import os
import sqlite3
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import core
import reports_lib
from core import CATEGORIES, DEVICE_TYPES, PRIORITIES, STATUSES, get_db


app = Flask(__name__, template_folder="client_templates", static_folder="static")
app.config["SECRET_KEY"] = core.get_secret_key("NIRMS_CLIENT_SECRET", ".secret_client")
app.config["SESSION_COOKIE_NAME"] = "nirms_client"     # keeps it separate from the admin app
app.teardown_appcontext(core.close_db)
core.init_security(app)


@app.before_request
def load_user():
    """Runs before every page: find out who is logged in (if anyone)."""
    g.user = core.load_user(session, "client")


def login_required(view):
    """Put @login_required above a page to send visitors who aren't logged in to /login."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def index():
    return redirect(url_for("dashboard" if g.user else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        office = request.form.get("office", "").strip() or "Individual (no office)"
        password = request.form.get("password", "")

        error = None
        if not name or not email:
            error = "Name and email are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."

        if error is None:
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO users (full_name, email, office, password_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (name, email, office, generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:          # email is UNIQUE in the database
                error = "That email is already registered."

        if error:
            flash(error, "error")
        else:
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        lock_key = "client:" + email
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if core.login_locked(lock_key):
            flash("Too many wrong attempts. Please wait 5 minutes and try again.", "error")
        elif user is None or not check_password_hash(user["password_hash"], password):
            core.login_failed(lock_key)
            flash("Incorrect email or password.", "error")
        elif user["role"] != "client":
            flash("NOC staff must sign in through the admin app.", "error")
        else:
            core.login_ok(lock_key)
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()

    # How many reports in each status (for the number cards)
    counts = {status: 0 for status in STATUSES}
    rows = db.execute(
        "SELECT status, COUNT(*) AS n FROM reports WHERE user_id = ? GROUP BY status",
        (g.user["id"],),
    )
    for row in rows:
        counts[row["status"]] = row["n"]

    # This client's reports, newest first
    reports = db.execute(
        "SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC, id DESC",
        (g.user["id"],),
    ).fetchall()

    return render_template("dashboard.html", counts=counts, reports=reports,
                           total=sum(counts.values()))


@app.route("/reports/new", methods=["GET", "POST"])
@login_required
def new_report():
    if request.method == "POST":
        category = request.form.get("category", "")
        priority = request.form.get("priority", "Medium")
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        device_type = request.form.get("device_type", "Not applicable")
        serial_no = request.form.get("serial_no", "").strip()

        if (category not in CATEGORIES or priority not in PRIORITIES
                or device_type not in DEVICE_TYPES or not location or not description):
            flash("Please complete all fields.", "error")
        else:
            db = get_db()
            ticket = core.next_ticket_no(db)
            cur = db.execute(
                "INSERT INTO reports (ticket_no, user_id, category, location, description, priority, "
                "device_type, serial_no, due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticket, g.user["id"], category, location, description, priority,
                 device_type, serial_no, core.default_due_date(priority)),
            )
            # First line of the report's history
            db.execute(
                "INSERT INTO report_updates (report_id, changed_by, note) VALUES (?, ?, ?)",
                (cur.lastrowid, g.user["full_name"], "Report submitted."),
            )
            db.commit()
            flash(f"Report submitted. Your ticket number is {ticket}.", "success")
            return redirect(url_for("report_detail", report_id=cur.lastrowid))

    return render_template("new_report.html", categories=CATEGORIES, priorities=PRIORITIES,
                           device_types=DEVICE_TYPES)

@app.route("/reports/export.pdf")
@login_required
def my_reports_pdf():
    """All of this client's own reports, as a PDF."""
    reports = get_db().execute("SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC",
                               (g.user["id"],)).fetchall()
    return send_file(reports_lib.client_pdf(g.user, reports), mimetype="application/pdf",
                     download_name="my-network-reports.pdf")


@app.route("/reports/<int:report_id>/pdf")
@login_required
def report_pdf(report_id):
    """One of this client's own tickets, with its tracking history, as a PDF."""
    db = get_db()
    report = db.execute(
        "SELECT r.*, u.full_name, u.email, u.office FROM reports r JOIN users u ON u.id = r.user_id "
        "WHERE r.id = ? AND r.user_id = ?", (report_id, g.user["id"])).fetchone()
    if report is None:
        abort(404)
    updates = db.execute("SELECT * FROM report_updates WHERE report_id = ? ORDER BY id",
                         (report_id,)).fetchall()
    return send_file(reports_lib.ticket_pdf(report, updates), mimetype="application/pdf",
                     download_name=f"{report['ticket_no']}.pdf")

@app.route("/reports/<int:report_id>")
@login_required
def report_detail(report_id):
    db = get_db()
    # "AND user_id = ?" means a client can only open their OWN reports
    report = db.execute(
        "SELECT * FROM reports WHERE id = ? AND user_id = ?", (report_id, g.user["id"])
    ).fetchone()
    if report is None:
        abort(404)
    updates = db.execute(
        "SELECT * FROM report_updates WHERE report_id = ? ORDER BY id DESC", (report_id,)
    ).fetchall()
    return render_template("report_detail.html", report=report, updates=updates)


if __name__ == "__main__":
     app.run(port=5000, debug=os.environ.get("NIRMS_DEBUG", "1") == "1")