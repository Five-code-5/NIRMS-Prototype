
import calendar
import io
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import CondPageBreak, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core import OVERDUE_SQL

BLUE = colors.HexColor("#1e3a8a")
GREY = colors.HexColor("#f4f6fa")
LINE = colors.HexColor("#c9d1dd")


# ======================================================================
# Periods
# ======================================================================
def _month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _quarter_start(d):
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _quarter(start):
    end = _add_months(start, 3) - timedelta(days=1)
    return start, end, f"Q{(start.month - 1) // 3 + 1} {start.year}"


def _month(start):
    return start, _month_end(start), start.strftime("%B %Y")


def period_range(name, start="", end="", today=None):
    """Return {'name','start','end','label','prev'} for the chosen period.
    'prev' is the period before it (for comparisons) or None."""
    today = today or date.today()
    if name == "last_month":
        s, e, label = _month(_add_months(today.replace(day=1), -1))
        prev = _month(_add_months(s, -1))
    elif name == "this_quarter":
        s, e, label = _quarter(_quarter_start(today))
        prev = _quarter(_add_months(s, -3))
    elif name == "last_quarter":
        s, e, label = _quarter(_add_months(_quarter_start(today), -3))
        prev = _quarter(_add_months(s, -3))
    elif name == "this_year":
        s, e, label = date(today.year, 1, 1), date(today.year, 12, 31), str(today.year)
        prev = (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), str(today.year - 1))
    elif name == "custom":
        try:
            s, e = date.fromisoformat(start), date.fromisoformat(end)
            if e < s:
                raise ValueError
            label, prev = f"{s:%b %d, %Y} to {e:%b %d, %Y}", None
        except ValueError:
            return period_range("this_month", today=today)
    else:
        name = "this_month"
        s, e, label = _month(today.replace(day=1))
        prev = _month(_add_months(s, -1))
    return {"name": name, "start": s.isoformat(), "end": e.isoformat(), "label": label,
            "prev": None if prev is None else
            {"start": prev[0].isoformat(), "end": prev[1].isoformat(), "label": prev[2]}}


# ======================================================================
# Numbers
# ======================================================================
def stats(db, start, end):
    """All the figures for reports CREATED between start and end (inclusive)."""
    p = (start, end)
    where = "date(r.created_at) BETWEEN ? AND ?"

    def count(extra=""):
        return db.execute(f"SELECT COUNT(*) FROM reports r WHERE {where} {extra}", p).fetchone()[0]

    def pairs(column, extra=""):
        rows = db.execute(
            f"SELECT r.{column}, COUNT(*) FROM reports r WHERE {where} {extra} "
            f"GROUP BY r.{column} ORDER BY COUNT(*) DESC", p).fetchall()
        return [(row[0], row[1]) for row in rows]

    total = count()
    resolved = count("AND r.status IN ('Resolved', 'Closed')")
    avg = db.execute(
        f"SELECT AVG(julianday(r.resolved_at) - julianday(r.created_at)) FROM reports r "
        f"WHERE {where} AND r.resolved_at != ''", p).fetchone()[0]
    with_due = count("AND r.resolved_at != '' AND r.due_date != ''")
    on_time = count("AND r.resolved_at != '' AND r.due_date != '' AND date(r.resolved_at) <= r.due_date")

    repeats = db.execute(
        f"SELECT r.location, r.category, COUNT(*) FROM reports r WHERE {where} "
        f"GROUP BY r.location, r.category HAVING COUNT(*) >= 2 ORDER BY COUNT(*) DESC", p
    ).fetchall()

    return {
        "total": total,
        "resolved": resolved,
        "rate": round(100 * resolved / total) if total else None,
        "avg_days": round(avg, 1) if avg is not None else None,
        "on_time_pct": round(100 * on_time / with_due) if with_due else None,
        "overdue": count(f"AND {OVERDUE_SQL}"),
        "by_category": pairs("category"),
        "by_priority": pairs("priority"),
        "by_repair": pairs("repair_type", "AND r.repair_type != ''"),
        "by_device": pairs("device_type", "AND r.device_type NOT IN ('', 'Not applicable')"),
        "repeats": [(r[0], r[1], r[2]) for r in repeats],
    }


def monthly_trend(db, months=6, today=None):
    """Reports received per month for the last few months: [(label, count), ...]."""
    today = today or date.today()
    out = []
    for i in range(months - 1, -1, -1):
        s = _add_months(today.replace(day=1), -i)
        n = db.execute("SELECT COUNT(*) FROM reports WHERE date(created_at) BETWEEN ? AND ?",
                       (s.isoformat(), _month_end(s).isoformat())).fetchone()[0]
        out.append((s.strftime("%b %Y"), n))
    return out


def bar_rows(pairs):
    """[(label, n)] -> [(label, n, percent_of_biggest)] for drawing bars."""
    biggest = max((n for _, n in pairs), default=0)
    return [(label or "(none)", n, round(100 * n / biggest) if biggest else 0) for label, n in pairs]


def _fmt(value, unit=""):
    return "—" if value is None else f"{value:g}{unit}"


def compare_rows(cur, prev):
    """Rows for the 'this period vs previous period' table."""
    spec = [("Reports received", "total", "", 0), ("Resolved", "resolved", "", 1),
            ("Resolution rate", "rate", "%", 1), ("Avg. days to resolve", "avg_days", " days", -1),
            ("Resolved on time", "on_time_pct", "%", 1)]
    rows = []
    for name, key, unit, good in spec:
        a, b = cur[key], prev[key]
        if a is None or b is None:
            delta, tone = "—", ""
        else:
            d = round(a - b, 1)
            delta = f"{d:+g}{unit}"
            tone = "" if d == 0 or good == 0 else ("good" if d * good > 0 else "bad")
        rows.append({"name": name, "cur": _fmt(a, unit), "prev": _fmt(b, unit),
                     "delta": delta, "tone": tone})
    return rows


# ======================================================================
# PDF files
# ======================================================================
_styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_styles["Title"], fontSize=17, textColor=BLUE, spaceAfter=2)
SUB = ParagraphStyle("SUB", parent=_styles["Normal"], fontSize=9.5, textColor=colors.grey,
                     alignment=1, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=_styles["Heading2"], fontSize=11.5, textColor=BLUE,
                    spaceBefore=12, spaceAfter=5, keepWithNext=1)
H2LONG = ParagraphStyle("H2LONG", parent=H2, keepWithNext=0)   # heading for tables that span pages
SMALL = ParagraphStyle("SMALL", parent=_styles["Normal"], fontSize=8.5, leading=10.5)
BODY = ParagraphStyle("BODY", parent=_styles["Normal"], fontSize=9.5, leading=12)


def P(text, style=SMALL):
    return Paragraph(escape(str(text if text not in (None, "") else "—")), style)


def _table(rows, widths, header=True):
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, LINE),
             ("FONTSIZE", (0, 0), (-1, -1), 8.5),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                  ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GREY])]
    table.setStyle(TableStyle(style))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(doc.leftMargin, 1.1 * cm, "NIRMS · DICT Region X · Network Issue Reporting and Monitoring System")
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 1.1 * cm, f"Page {doc.page}")
    canvas.restoreState()


def _build(story, title, pagesize=A4, side=2 * cm):
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=side, rightMargin=side, topMargin=1.8 * cm,
                      bottomMargin=1.8 * cm, title=title).build(story, onFirstPage=_footer,
                                                                 onLaterPages=_footer)
    buf.seek(0)
    return buf


def _header(title, subtitle):
    return [Paragraph(escape(title), H1),
            Paragraph(escape(subtitle) + " · generated " + datetime.now().strftime("%B %d, %Y %I:%M %p"), SUB)]


def _signatures():
    rows = [["", "", ""],
            ["Prepared by (NOC staff) / date", "", "Noted by (head / supervisor) / date"]]
    t = Table(rows, colWidths=[7.5 * cm, 2 * cm, 7.5 * cm], rowHeights=[1.3 * cm, 0.6 * cm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("TEXTCOLOR", (0, 1), (-1, 1), colors.grey),
                           ("LINEABOVE", (0, 1), (0, 1), 0.6, colors.black),
                           ("LINEABOVE", (2, 1), (2, 1), 0.6, colors.black)]))
    return [Spacer(1, 18), KeepTogether([t])]


def summary_pdf(label, s, prev_label=None, prev=None, tickets=None):
    story = _header("Network Issue Summary Report", f"DICT Region X NOC · {label}")

    story.append(Paragraph("Key figures", H2))
    story.append(_table([
        ["Received", "Resolved", "Resolution rate", "Avg. days", "On time", "Overdue now"],
        [_fmt(s["total"]), _fmt(s["resolved"]), _fmt(s["rate"], "%"), _fmt(s["avg_days"]),
         _fmt(s["on_time_pct"], "%"), _fmt(s["overdue"])]], [2.8 * cm] * 6))

    if prev is not None:
        story.append(Paragraph(f"Comparison with {prev_label}", H2))
        rows = [["Measure", label, prev_label, "Change"]]
        rows += [[r["name"], r["cur"], r["prev"], r["delta"]] for r in compare_rows(s, prev)]
        story.append(_table(rows, [6 * cm, 3.5 * cm, 3.5 * cm, 3.5 * cm]))

    for title, key, head in [("Issues by type", "by_category", "Issue type"),
                             ("Issues by priority", "by_priority", "Priority"),
                             ("Devices involved", "by_device", "Device"),
                             ("How issues were handled", "by_repair", "Repair type")]:
        if s[key]:
            story.append(Paragraph(title, H2))
            story.append(_table([[head, "Reports"]] + [[P(a), str(b)] for a, b in s[key]], [11 * cm, 3 * cm]))

    story.append(Paragraph("Repeat problems (same place and issue type, 2 or more times)", H2))
    if s["repeats"]:
        story.append(_table([["Location", "Issue type", "Times"]] +
                            [[P(a), P(b), str(c)] for a, b, c in s["repeats"]], [7.5 * cm, 6 * cm, 2.5 * cm]))
    else:
        story.append(Paragraph("No repeat problems in this period.", BODY))
    if tickets is not None:
        story += [CondPageBreak(4 * cm), Paragraph(f"Ticket log ({len(tickets)} tickets received in this period)", H2LONG)]
        rows = [["Ticket", "Date", "Issue / Location", "Priority", "Status", "Resolved"]]
        for r in tickets:
            rows.append([P(r["ticket_no"]), P(r["created_at"][:10]), P(f"{r['category']} - {r['location']}"),
                         P(r["priority"]), P(r["status"]), P(r["resolved_at"][:10])])
        if len(rows) == 1:
            story.append(Paragraph("No tickets in this period.", BODY))
        else:
            story.append(_table(rows, [3.3 * cm, 2.0 * cm, 5.6 * cm, 1.8 * cm, 2.1 * cm, 2.2 * cm]))
    return _build(story + _signatures(), f"NIRMS summary {label}")


def register_pdf(subtitle, rows):
    """Ticket register: one line per ticket, landscape. rows come from the dashboard query
    (they need full_name, office and overdue)."""
    story = _header("Ticket Register", subtitle)
    open_n = sum(1 for r in rows if r["status"] in ("Open", "In Progress"))
    late_n = sum(1 for r in rows if r["overdue"])
    story.append(_table([["Tickets listed", "Still open", "Resolved / closed", "Overdue"],
                         [str(len(rows)), str(open_n), str(len(rows) - open_n), str(late_n)]], [4.5 * cm] * 4))
    story += [CondPageBreak(4 * cm), Paragraph("Tickets", H2LONG)]
    table = [["Ticket", "Submitted", "Client / Office", "Issue", "Location", "Priority", "Status",
              "Due", "Assigned", "Resolved"]]
    for r in rows:
        table.append([P(r["ticket_no"]), P(r["created_at"][:10]), P(f"{r['full_name']} ({r['office']})"),
                      P(f"{r['category']}" + (f" / {r['device_type']}" if r["device_type"] not in ("", "Not applicable") else "")),
                      P(r["location"]), P(r["priority"]), P(r["status"]),
                      P(r["due_date"] + (" (OVERDUE)" if r["overdue"] else "")),
                      P(r["assigned_to"]), P(r["resolved_at"][:10])])
    if len(table) == 1:
        story.append(Paragraph("No tickets match this filter.", BODY))
    else:
        story.append(_table(table, [3.3 * cm, 2.1 * cm, 4.0 * cm, 3.2 * cm, 3.6 * cm, 1.6 * cm, 1.9 * cm,
                                    2.3 * cm, 2.3 * cm, 2.2 * cm]))
    return _build(story + _signatures(), "NIRMS ticket register", pagesize=landscape(A4), side=1.5 * cm)


def ticket_pdf(r, updates):
    story = _header(f"Service Ticket {r['ticket_no']}", "DICT Region X NOC")
    rows = [
        ["Ticket no.", P(r["ticket_no"]), "Status", P(r["status"])],
        ["Reported by", P(f"{r['full_name']} ({r['office']})"), "Priority", P(r["priority"])],
        ["Issue type", P(r["category"]), "Submitted", P(r["created_at"])],
        ["Location", P(r["location"]), "Due date", P(r["due_date"])],
        ["Device", P(r["device_type"]), "Serial no.", P(r["serial_no"])],
        ["Assigned to", P(r["assigned_to"]), "Resolved on", P(r["resolved_at"])],
        ["Repair type", P(r["repair_type"]), "Pre-test / Post-test", P(f"{r['pre_test']} / {r['post_test']}")],
    ]
    t = _table(rows, [3 * cm, 5.5 * cm, 3.5 * cm, 5 * cm], header=False)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), GREY), ("BACKGROUND", (2, 0), (2, -1), GREY),
                           ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold")]))
    story.append(t)
    for title, text in [("Problem described by the client", r["description"]),
                        ("Action taken", r["action_taken"]), ("NOC remarks", r["remarks"])]:
        story += [Paragraph(title, H2), Paragraph(escape(text or "—"), BODY)]
    story.append(Paragraph("History", H2))
    story.append(_table([["Date", "By", "What happened"]] +
                        [[P(u["created_at"]), P(u["changed_by"]), P(u["note"])] for u in updates],
                        [3.6 * cm, 3.6 * cm, 9.8 * cm]))
    return _build(story + _signatures(), f"Ticket {r['ticket_no']}")


def client_pdf(client, reports):
    story = _header(f"Service History: {client['full_name']}",
                    f"{client['office']} · {client['email']}")
    open_n = sum(1 for r in reports if r["status"] in ("Open", "In Progress"))
    story.append(_table([["Total reports", "Still open", "Resolved / closed"],
                         [str(len(reports)), str(open_n), str(len(reports) - open_n)]], [5 * cm] * 3))
    story += [CondPageBreak(4 * cm), Paragraph("All reports", H2LONG)]
    rows = [["Ticket", "Date", "Issue", "Device / Serial", "Status", "Resolved"]]
    for r in reports:
        rows.append([P(r["ticket_no"]), P(r["created_at"][:10]), P(f"{r['category']} - {r['location']}"),
                     P(f"{r['device_type']} {r['serial_no']}".strip()), P(r["status"]),
                     P(r["resolved_at"][:10])])
    story.append(_table(rows, [3.3 * cm, 2.0 * cm, 4.6 * cm, 3.1 * cm, 2.0 * cm, 2.0 * cm]))
    return _build(story + _signatures(), f"History {client['full_name']}")