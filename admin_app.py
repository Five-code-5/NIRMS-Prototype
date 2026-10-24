import csv
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

import click
from flask import (Flask, Response, abort, flash, g, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import core
import reports_lib
from core import (CATEGORIES, OVERDUE_SQL, PRIORITIES, REPAIR_TYPES, STATUSES,
                  TEST_RESULTS, get_db)

app = Flask(__name__, template_folder="admin_templates", static_folder="static")
app.config["SECRET_KEY"] = core.get_secret_key("NIRMS_ADMIN_SECRET", ".secret_admin")
app.config["SESSION_COOKIE_NAME"] = "nirms_admin"    # different from the client app
app.teardown_appcontext(core.close_db)
core.init_security(app)


@app.before_request
def load_user():
    g.user = core.load_user(session, "admin")
    # Still using the starting password? Make them change it before anything else.
    if (g.user is not None and session.get("must_change")
            and request.endpoint not in ("change_password", "logout", "static")):
        return redirect(url_for("change_password"))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.cli.command("add-admin")
@click.option("--name", prompt="Full name")
@click.option("--email", prompt="Email")
@click.password_option()
def add_admin(name, email, password):
    """Create another NOC admin account."""
    db = sqlite3.connect(core.DB_PATH)
    try:
        db.execute(
            "INSERT INTO users (full_name, email, office, password_hash, role) "
            "VALUES (?, ?, 'DICT Region X - NOC', ?, 'admin')",
            (name, email.strip().lower(), generate_password_hash(password)),
        )
        db.commit()
        click.echo("Admin created.")
    except sqlite3.IntegrityError:
        click.echo("That email already exists.")
    finally:
        db.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        lock_key = "admin:" + email
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if core.login_locked(lock_key):
            flash("Too many wrong attempts. Please wait 5 minutes and try again.", "error")
        elif user is None or not check_password_hash(user["password_hash"], password):
            core.login_failed(lock_key)
            flash("Incorrect email or password.", "error")
        elif user["role"] != "admin":
            flash("This app is for NOC staff only. Clients use the client app.", "error")
        else:
            core.login_ok(lock_key)
            session.clear()
            session["user_id"] = user["id"]
            session["must_change"] = check_password_hash(user["password_hash"], "admin123")
            return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/password", methods=["GET", "POST"])
@admin_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        again = request.form.get("again", "")
        if not check_password_hash(g.user["password_hash"], current):
            flash("Your current password is not correct.", "error")
        elif len(new) < 8:
            flash("The new password must be at least 8 characters.", "error")
        elif new == "admin123":
            flash("Please choose a different password from the starting one.", "error")
        elif new != again:
            flash("The two new passwords do not match.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(new), g.user["id"]))
            db.commit()
            session.pop("must_change", None)
            flash("Password changed.", "success")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html", forced=bool(session.get("must_change")))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _report_filters():
    """Read the filters from the web address (?status=...&q=...) so the screen, the PDF and the
    CSV export all show the same list."""
    status = request.args.get("status", "")
    category = request.args.get("category", "")
    priority = request.args.get("priority", "")
    q = request.args.get("q", "").strip()
    only_overdue = request.args.get("overdue") == "1"

    where, params = [], []
    if status in STATUSES:
        where.append("r.status = ?"); params.append(status)
    if category in CATEGORIES:
        where.append("r.category = ?"); params.append(category)
    if priority in PRIORITIES:
        where.append("r.priority = ?"); params.append(priority)
    if only_overdue:
        where.append(OVERDUE_SQL)
    if q:
        where.append("(r.ticket_no LIKE ? OR r.location LIKE ? OR r.description LIKE ? "
                     "OR u.full_name LIKE ? OR u.office LIKE ?)")
        params += [f"%{q}%"] * 5
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    filters = {"status": status, "category": category, "priority": priority, "q": q,
               "overdue": only_overdue}
    return clause, params, filters


def _filtered_reports(db):
    clause, params, filters = _report_filters()
    rows = db.execute(
        f"SELECT r.*, u.full_name, u.office, CASE WHEN {OVERDUE_SQL} THEN 1 ELSE 0 END AS overdue "
        "FROM reports r JOIN users u ON u.id = r.user_id "
        f"{clause} ORDER BY CASE r.priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 "
        "WHEN 'Medium' THEN 2 ELSE 3 END, r.created_at DESC",
        params,
    ).fetchall()
    return rows, filters


def _filter_text(filters):
    parts = [f"{k}: {v}" for k, v in (("Status", filters["status"]), ("Issue type", filters["category"]),
                                      ("Priority", filters["priority"]), ("Search", filters["q"])) if v]
    if filters["overdue"]:
        parts.append("Overdue only")
    return ", ".join(parts) if parts else "All tickets"


@app.route("/reports")
@admin_required
def reports_list():
    """All Reports: the full ticket list with search, filters and PDF/CSV export."""
    db = get_db()
    reports, filters = _filtered_reports(db)
    overdue_total = db.execute(f"SELECT COUNT(*) FROM reports r WHERE {OVERDUE_SQL}").fetchone()[0]
    return render_template(
        "reports.html", reports=reports, overdue_total=overdue_total,
        categories=CATEGORIES, priorities=PRIORITIES, statuses=STATUSES, filters=filters,
    )


# ---------------- Dashboard (KPIs and charts) ----------------
PERIOD_BUTTONS = [("today", "Today"), ("7d", "7 Days"), ("30d", "30 Days"),
                  ("90d", "90 Days"), ("year", "This Year"), ("all", "All time")]


def _dash_period():
    """Work out the dates for the dashboard from ?period=... or ?start=...&end=..."""
    today = date.today()
    start, end = request.args.get("start", ""), request.args.get("end", "")
    if start and end:
        try:
            s, e = date.fromisoformat(start), date.fromisoformat(end)
            if s <= e:
                return "custom", s, e
        except ValueError:
            pass
    name = request.args.get("period", "30d")
    if name == "today":
        return name, today, today
    if name == "7d":
        return name, today - timedelta(days=6), today
    if name == "90d":
        return name, today - timedelta(days=89), today
    if name == "year":
        return name, date(today.year, 1, 1), today
    if name == "all":
        return name, date(2000, 1, 1), today
    return "30d", today - timedelta(days=29), today


def _pct(part, whole):
    return round(100 * part / whole) if whole else None


@app.route("/")
@admin_required
def dashboard():
    db = get_db()
    name, s, e = _dash_period()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    category = request.args.get("category", "")

    where, params = ["date(r.created_at) BETWEEN ? AND ?"], [s.isoformat(), e.isoformat()]
    for column, value, allowed in (("status", status, STATUSES), ("priority", priority, PRIORITIES),
                                   ("category", category, CATEGORIES)):
        if value in allowed:
            where.append(f"r.{column} = ?"); params.append(value)
        else:
            if column == "status": status = ""
            elif column == "priority": priority = ""
            else: category = ""
    W = " AND ".join(where)

    def one(sql, extra=()):
        return db.execute(sql, tuple(params) + tuple(extra)).fetchone()[0]

    total = one(f"SELECT COUNT(*) FROM reports r WHERE {W}")
    resolved = one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND r.status IN ('Resolved','Closed')")
    backlog = one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND r.status IN ('Open','In Progress')")
    overdue = one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND {OVERDUE_SQL}")
    avg_days = one(f"SELECT AVG(julianday(r.resolved_at) - julianday(r.created_at)) FROM reports r "
                   f"WHERE {W} AND r.resolved_at != ''")
    with_due = one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND r.resolved_at != '' AND r.due_date != ''")
    on_time = one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND r.resolved_at != '' AND r.due_date != '' "
                  "AND date(r.resolved_at) <= r.due_date")

    # On-time rate for each priority (the NIRMS version of "SLA by priority")
    sla = []
    for p in reversed(PRIORITIES):      # Critical first
        w = db.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN date(r.resolved_at) <= r.due_date THEN 1 ELSE 0 END) "
            f"FROM reports r WHERE {W} AND r.priority = ? AND r.resolved_at != '' AND r.due_date != ''",
            tuple(params) + (p,)).fetchone()
        sla.append({"name": p, "pct": _pct(w[1] or 0, w[0]), "n": w[0]})

    # Open tickets by age
    aging = [("0-1 day", 0, 1), ("1-3 days", 1, 3), ("3-7 days", 3, 7), ("7+ days", 7, 100000)]
    aging_rows = []
    for label, lo, hi in aging:
        n = one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND r.status IN ('Open','In Progress') "
                "AND julianday('now','localtime') - julianday(r.created_at) >= ? "
                "AND julianday('now','localtime') - julianday(r.created_at) < ?", (lo, hi))
        aging_rows.append({"label": label, "n": n})
    aging_max = max((a["n"] for a in aging_rows), default=0)

    # Technician leaderboard
    board = []
    for row in db.execute(
            f"SELECT r.assigned_to AS who, "
            "SUM(CASE WHEN r.status IN ('Resolved','Closed') THEN 1 ELSE 0 END) AS done, "
            "SUM(CASE WHEN r.status IN ('Open','In Progress') THEN 1 ELSE 0 END) AS open_n, "
            "AVG(CASE WHEN r.resolved_at != '' THEN julianday(r.resolved_at) - julianday(r.created_at) END) AS avg_d, "
            "SUM(CASE WHEN r.resolved_at != '' AND r.due_date != '' THEN 1 ELSE 0 END) AS due_n, "
            "SUM(CASE WHEN r.resolved_at != '' AND r.due_date != '' AND date(r.resolved_at) <= r.due_date "
            "THEN 1 ELSE 0 END) AS ok_n "
            f"FROM reports r WHERE {W} AND r.assigned_to != '' GROUP BY r.assigned_to "
            "ORDER BY done DESC, open_n ASC", tuple(params)):
        board.append({"who": row["who"], "done": row["done"], "open": row["open_n"],
                      "avg": None if row["avg_d"] is None else round(row["avg_d"], 1),
                      "pct": _pct(row["ok_n"], row["due_n"])})
    unassigned = one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND r.assigned_to = '' "
                     "AND r.status IN ('Open','In Progress')")

    cats = [(r[0], r[1]) for r in db.execute(
        f"SELECT r.category, COUNT(*) FROM reports r WHERE {W} GROUP BY r.category "
        "ORDER BY COUNT(*) DESC LIMIT 6", tuple(params))]
    cat_max = max((n for _, n in cats), default=0)

    statuses = {st: one(f"SELECT COUNT(*) FROM reports r WHERE {W} AND r.status = ?", (st,))
                for st in STATUSES}
    recent = db.execute(
        f"SELECT r.*, u.full_name, u.office, CASE WHEN {OVERDUE_SQL} THEN 1 ELSE 0 END AS overdue "
        f"FROM reports r JOIN users u ON u.id = r.user_id WHERE {W} ORDER BY r.created_at DESC LIMIT 6",
        tuple(params)).fetchall()

    return render_template(
        "dashboard.html", period_name=name, start=s.isoformat(), end=e.isoformat(),
        period_buttons=PERIOD_BUTTONS, status=status, priority=priority, category=category,
        statuses=STATUSES, priorities=PRIORITIES, categories=CATEGORIES,
        kpi={"total": total, "backlog": backlog, "resolved": resolved, "overdue": overdue,
             "avg_days": None if avg_days is None else round(avg_days, 1),
             "on_time": _pct(on_time, with_due), "rate": _pct(resolved, total)},
        sla=sla, aging=aging_rows, aging_max=aging_max, board=board, unassigned=unassigned,
        cats=cats, cat_max=cat_max, status_counts=statuses, recent=recent,
    )


@app.route("/reports/export.pdf")
@admin_required
def export_pdf():
    """Ticket register as a PDF - the same list (and filters) as the dashboard."""
    rows, filters = _filtered_reports(get_db())
    return send_file(reports_lib.register_pdf(f"DICT Region X NOC · {_filter_text(filters)}", rows),
                     mimetype="application/pdf",
                     download_name=f"NIRMS-ticket-register-{date.today().isoformat()}.pdf")


@app.route("/reports/export.csv")
@admin_required
def export_csv():
    """The same list as a CSV file (opens in Excel)."""
    rows, _ = _filtered_reports(get_db())
    out = io.StringIO()
    out.write("\ufeff")                       # lets Excel read special characters correctly
    w = csv.writer(out)
    w.writerow(["Ticket", "Submitted", "Client", "Office", "Issue type", "Device", "Serial/tag", "Location",
                "Priority", "Status", "Due date", "Overdue", "Assigned to", "Repair type", "Pre-test",
                "Post-test", "Action taken", "Remarks", "Resolved", "Description"])
    for r in rows:
        w.writerow([r["ticket_no"], r["created_at"], r["full_name"], r["office"], r["category"],
                    r["device_type"], r["serial_no"], r["location"], r["priority"], r["status"],
                    r["due_date"], "Yes" if r["overdue"] else "", r["assigned_to"], r["repair_type"],
                    r["pre_test"], r["post_test"], r["action_taken"], r["remarks"], r["resolved_at"],
                    r["description"]])
    return Response(out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=NIRMS-tickets-{date.today().isoformat()}.csv"})


def fetch_report_or_404(report_id):
    row = get_db().execute(
        "SELECT r.*, u.full_name, u.email, u.office FROM reports r "
        "JOIN users u ON u.id = r.user_id WHERE r.id = ?", (report_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/reports/<int:report_id>", methods=["GET", "POST"])
@admin_required
def edit_report(report_id):
    report = fetch_report_or_404(report_id)
    db = get_db()
    if request.method == "POST":
        form = request.form
        status = form.get("status", report["status"])
        priority = form.get("priority", report["priority"])
        repair_type = form.get("repair_type", "")
        pre_test = form.get("pre_test", "Not done")
        post_test = form.get("post_test", "Not done")
        due_date = form.get("due_date", "").strip()
        assigned_to = form.get("assigned_to", "").strip()
        action_taken = form.get("action_taken", "").strip()
        remarks = form.get("remarks", "").strip()

        if (status not in STATUSES or priority not in PRIORITIES or repair_type not in REPAIR_TYPES
                or pre_test not in TEST_RESULTS or post_test not in TEST_RESULTS):
            abort(400)
        if due_date:
            try:
                date.fromisoformat(due_date)
            except ValueError:
                abort(400)

        # Remember WHEN it was resolved (used for the reports); clear it if reopened
        resolved_at = report["resolved_at"]
        if status in ("Resolved", "Closed") and not resolved_at:
            resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elif status in ("Open", "In Progress"):
            resolved_at = ""

        # Write down exactly what changed, so the client can see it
        changes = []
        for label, old, new in [("Status", report["status"], status),
                                ("Priority", report["priority"], priority),
                                ("Due date", report["due_date"], due_date),
                                ("Assigned to", report["assigned_to"], assigned_to),
                                ("Repair type", report["repair_type"], repair_type),
                                ("Pre-test", report["pre_test"], pre_test),
                                ("Post-test", report["post_test"], post_test)]:
            if old != new:
                changes.append(f"{label}: {old or 'none'} -> {new or 'none'}")
        if action_taken != report["action_taken"]:
            changes.append("Action taken updated")
        if remarks != report["remarks"]:
            changes.append("Remarks updated")

        db.execute(
            "UPDATE reports SET status = ?, priority = ?, due_date = ?, assigned_to = ?, "
            "repair_type = ?, pre_test = ?, post_test = ?, action_taken = ?, remarks = ?, "
            "resolved_at = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (status, priority, due_date, assigned_to, repair_type, pre_test, post_test,
             action_taken, remarks, resolved_at, report_id),
        )
        if changes:
            note = "; ".join(changes)
            if remarks:
                note += f". Remarks: {remarks}"
            db.execute(
                "INSERT INTO report_updates (report_id, changed_by, note) VALUES (?, ?, ?)",
                (report_id, g.user["full_name"], note),
            )
        db.commit()
        flash("Report updated.", "success")
        return redirect(url_for("reports_list"))

    updates = db.execute(
        "SELECT * FROM report_updates WHERE report_id = ? ORDER BY id DESC", (report_id,)
    ).fetchall()
    return render_template("edit.html", report=report, updates=updates, priorities=PRIORITIES,
                           statuses=STATUSES, repair_types=REPAIR_TYPES, test_results=TEST_RESULTS)


@app.route("/reports/<int:report_id>/pdf")
@admin_required
def report_pdf(report_id):
    report = fetch_report_or_404(report_id)
    updates = get_db().execute(
        "SELECT * FROM report_updates WHERE report_id = ? ORDER BY id", (report_id,)).fetchall()
    return send_file(reports_lib.ticket_pdf(report, updates), mimetype="application/pdf",
                     download_name=f"{report['ticket_no']}.pdf")


# ---------------- Reports and charts (analytics) ----------------
def _period_from_request():
    return reports_lib.period_range(request.args.get("period", "this_month"),
                                    request.args.get("start", ""), request.args.get("end", ""))


@app.route("/analytics")
@admin_required
def analytics():
    db = get_db()
    period = _period_from_request()
    cur = reports_lib.stats(db, period["start"], period["end"])
    prev = compare = None
    if period["prev"]:
        prev = reports_lib.stats(db, period["prev"]["start"], period["prev"]["end"])
        compare = reports_lib.compare_rows(cur, prev)
    return render_template(
        "analytics.html", period=period, s=cur, compare=compare,
        category_bars=reports_lib.bar_rows(cur["by_category"]),
        priority_bars=reports_lib.bar_rows(cur["by_priority"]),
        repair_bars=reports_lib.bar_rows(cur["by_repair"]),
        device_bars=reports_lib.bar_rows(cur["by_device"]),
        trend_bars=reports_lib.bar_rows(reports_lib.monthly_trend(db)),
        args=request.args,
    )


@app.route("/analytics/pdf")
@admin_required
def analytics_pdf():
    db = get_db()
    period = _period_from_request()
    cur = reports_lib.stats(db, period["start"], period["end"])
    prev = prev_label = None
    if period["prev"]:
        prev = reports_lib.stats(db, period["prev"]["start"], period["prev"]["end"])
        prev_label = period["prev"]["label"]
    tickets = db.execute("SELECT * FROM reports WHERE date(created_at) BETWEEN ? AND ? ORDER BY created_at",
                         (period["start"], period["end"])).fetchall()
    return send_file(reports_lib.summary_pdf(period["label"], cur, prev_label, prev, tickets),
                     mimetype="application/pdf",
                     download_name=f"NIRMS-summary-{period['start']}-to-{period['end']}.pdf")


# ---------------- Clients (service history per end user) ----------------
@app.route("/clients")
@admin_required
def clients():
    rows = get_db().execute(
        "SELECT u.*, COUNT(r.id) AS n, "
        "SUM(CASE WHEN r.status IN ('Open', 'In Progress') THEN 1 ELSE 0 END) AS open_n "
        "FROM users u LEFT JOIN reports r ON r.user_id = u.id WHERE u.role = 'client' "
        "GROUP BY u.id ORDER BY n DESC, u.full_name").fetchall()
    return render_template("clients.html", clients=rows)


def _client_or_404(client_id):
    db = get_db()
    client = db.execute("SELECT * FROM users WHERE id = ? AND role = 'client'", (client_id,)).fetchone()
    if client is None:
        abort(404)
    reports = db.execute("SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC",
                         (client_id,)).fetchall()
    return client, reports


@app.route("/clients/<int:client_id>")
@admin_required
def client_detail(client_id):
    client, reports = _client_or_404(client_id)
    return render_template("client_detail.html", client=client, reports=reports)


@app.route("/clients/<int:client_id>/pdf")
@admin_required
def client_pdf(client_id):
    client, reports = _client_or_404(client_id)
    return send_file(reports_lib.client_pdf(client, reports), mimetype="application/pdf",
                     download_name=f"history-{client['id']}.pdf")


@app.route("/clients/<int:client_id>/reset-password", methods=["POST"])
@admin_required
def reset_client_password(client_id):
    """For clients who forgot their password: set a temporary one and tell the NOC staff."""
    client, _ = _client_or_404(client_id)
    temp = secrets.token_urlsafe(6)
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(temp), client_id))
    db.commit()
    flash(f"Temporary password for {client['full_name']}: {temp}  (give it to them and ask them to keep it private)",
          "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/reports/<int:report_id>/delete", methods=["POST"])
@admin_required
def delete_report(report_id):
    fetch_report_or_404(report_id)
    db = get_db()
    db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    db.commit()
    flash("Report deleted.", "success")
    return redirect(url_for("reports_list"))


if __name__ == "__main__":
    app.run(port=5001, debug=os.environ.get("NIRMS_DEBUG", "1") == "1")