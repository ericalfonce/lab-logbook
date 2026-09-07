import os
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_file, g
)
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logbook.db")

ADMIN_PASSWORD = os.environ.get("LAB_ADMIN_PASSWORD", "admin123")
IDLE_TIMEOUT_MINUTES = int(os.environ.get("LAB_IDLE_TIMEOUT", "120"))
DEFAULT_NUM_COMPUTERS = int(os.environ.get("LAB_NUM_COMPUTERS", "30"))


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS computers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number INTEGER UNIQUE NOT NULL,
            label TEXT,
            status TEXT NOT NULL DEFAULT 'free'
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            user_class TEXT,
            computer_id INTEGER NOT NULL,
            check_in_time TEXT NOT NULL,
            check_out_time TEXT,
            duration_minutes REAL,
            FOREIGN KEY (computer_id) REFERENCES computers(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_computer ON sessions(computer_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_checkin ON sessions(check_in_time);
        CREATE INDEX IF NOT EXISTS idx_sessions_checkout ON sessions(check_out_time);
    """)
    count = db.execute("SELECT COUNT(*) FROM computers").fetchone()[0]
    if count == 0:
        for i in range(1, DEFAULT_NUM_COMPUTERS + 1):
            db.execute(
                "INSERT INTO computers (number, label, status) VALUES (?, ?, 'free')",
                (i, f"PC {i}"),
            )
    db.commit()
    db.close()


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    db = get_db()
    computers = db.execute(
        "SELECT id, number, label, status FROM computers ORDER BY number"
    ).fetchall()

    active_sessions = db.execute("""
        SELECT s.id, s.user_name, s.user_class, s.check_in_time, s.computer_id,
               c.number as comp_number, c.label as comp_label
        FROM sessions s
        JOIN computers c ON s.computer_id = c.id
        WHERE s.check_out_time IS NULL
        ORDER BY s.check_in_time DESC
    """).fetchall()

    free_computers = [c for c in computers if c["status"] == "free"]

    class_suggestions = [
        r[0] for r in db.execute(
            "SELECT DISTINCT user_class FROM sessions WHERE user_class IS NOT NULL ORDER BY user_class"
        ).fetchall()
    ]
    common_classes = [
        "Form 1A", "Form 1B", "Form 2A", "Form 2B",
        "Form 3A", "Form 3B", "Form 4A", "Form 4B",
        "Form 5A", "Form 5B", "Form 6A", "Form 6B",
        "Certificate Yr 1", "Certificate Yr 2",
        "Diploma Yr 1", "Diploma Yr 2",
    ]
    all_classes = sorted(set(class_suggestions + common_classes))

    auto_logout_threshold = (
        datetime.now() - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
    ).isoformat()

    return render_template(
        "index.html",
        computers=computers,
        free_computers=free_computers,
        active_sessions=active_sessions,
        class_suggestions=all_classes,
        auto_logout_threshold=auto_logout_threshold,
    )


@app.route("/checkin", methods=["POST"])
def checkin():
    db = get_db()
    user_name = request.form.get("user_name", "").strip()
    user_class = request.form.get("user_class", "").strip() or None
    computer_id = request.form.get("computer_id")

    if not user_name or not computer_id:
        flash("Please enter your name and select a computer.", "error")
        return redirect(url_for("index"))

    computer = db.execute(
        "SELECT id, status FROM computers WHERE id = ?", (computer_id,)
    ).fetchone()
    if not computer or computer["status"] != "free":
        flash("That computer is not available.", "error")
        return redirect(url_for("index"))

    already_checked_in = db.execute(
        "SELECT id FROM sessions WHERE user_name = ? AND check_out_time IS NULL",
        (user_name,),
    ).fetchone()
    if already_checked_in:
        flash("You are already checked in. Please check out first.", "error")
        return redirect(url_for("index"))

    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO sessions (user_name, user_class, computer_id, check_in_time) VALUES (?, ?, ?, ?)",
        (user_name, user_class, computer_id, now),
    )
    db.execute(
        "UPDATE computers SET status = 'occupied' WHERE id = ?", (computer_id,)
    )
    db.commit()

    flash(f"Checked in! Welcome, {user_name}.", "success")
    return redirect(url_for("index"))


@app.route("/checkout/<int:session_id>", methods=["POST"])
def checkout(session_id):
    db = get_db()
    sess = db.execute(
        "SELECT * FROM sessions WHERE id = ? AND check_out_time IS NULL",
        (session_id,),
    ).fetchone()
    if not sess:
        flash("Session not found or already checked out.", "error")
        return redirect(url_for("index"))

    now = datetime.now()
    check_in = datetime.fromisoformat(sess["check_in_time"])
    duration = round((now - check_in).total_seconds() / 60, 1)

    db.execute(
        "UPDATE sessions SET check_out_time = ?, duration_minutes = ? WHERE id = ?",
        (now.isoformat(), duration, session_id),
    )
    db.execute(
        "UPDATE computers SET status = 'free' WHERE id = ?",
        (sess["computer_id"],),
    )
    db.commit()

    flash(f"Checked out. Duration: {duration:.0f} minutes.", "success")
    return redirect(url_for("index"))


@app.route("/auto-logout", methods=["POST"])
def auto_logout():
    db = get_db()
    threshold = (
        datetime.now() - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
    ).isoformat()

    stale = db.execute(
        "SELECT id, computer_id, check_in_time FROM sessions WHERE check_out_time IS NULL AND check_in_time < ?",
        (threshold,),
    ).fetchall()

    now = datetime.now()
    count = 0
    for sess in stale:
        check_in = datetime.fromisoformat(sess["check_in_time"])
        duration = round((now - check_in).total_seconds() / 60, 1)
        db.execute(
            "UPDATE sessions SET check_out_time = ?, duration_minutes = ? WHERE id = ?",
            (now.isoformat(), duration, sess["id"]),
        )
        db.execute(
            "UPDATE computers SET status = 'free' WHERE id = ?",
            (sess["computer_id"],),
        )
        count += 1
    db.commit()

    if count:
        flash(f"Auto-logged out {count} idle session(s).", "info")
    return redirect(url_for("index"))


@app.route("/history")
def history():
    db = get_db()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    computer_filter = request.args.get("computer", "")
    search = request.args.get("search", "")

    query = """
        SELECT s.id, s.user_name, s.user_class, s.check_in_time, s.check_out_time,
               s.duration_minutes, c.number as comp_number, c.label as comp_label
        FROM sessions s
        JOIN computers c ON s.computer_id = c.id
        WHERE 1=1
    """
    params = []

    if date_from:
        query += " AND s.check_in_time >= ?"
        params.append(date_from + "T00:00:00")
    if date_to:
        query += " AND s.check_in_time <= ?"
        params.append(date_to + "T23:59:59")
    if computer_filter:
        query += " AND c.number = ?"
        params.append(computer_filter)
    if search:
        query += " AND s.user_name LIKE ?"
        params.append(f"%{search}%")

    query += " ORDER BY s.check_in_time DESC LIMIT 500"

    sessions = db.execute(query, params).fetchall()
    computers = db.execute("SELECT number FROM computers ORDER BY number").fetchall()

    return render_template(
        "history.html",
        sessions=sessions,
        computers=computers,
        date_from=date_from,
        date_to=date_to,
        computer_filter=computer_filter,
        search=search,
    )


@app.route("/reports")
def reports():
    db = get_db()
    period = request.args.get("period", "daily")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    today = datetime.now().date()
    if not date_from or not date_to:
        if period == "daily":
            date_from = today.isoformat()
            date_to = today.isoformat()
        elif period == "weekly":
            date_from = (today - timedelta(days=today.weekday())).isoformat()
            date_to = today.isoformat()
        elif period == "monthly":
            date_from = today.replace(day=1).isoformat()
            date_to = today.isoformat()

    query = """
        SELECT s.user_name, s.user_class, s.check_in_time, s.check_out_time,
               s.duration_minutes, c.number as comp_number
        FROM sessions s
        JOIN computers c ON s.computer_id = c.id
        WHERE s.check_in_time >= ? AND s.check_in_time <= ?
        ORDER BY s.check_in_time
    """
    sessions = db.execute(
        query, (date_from + "T00:00:00", date_to + "T23:59:59")
    ).fetchall()

    total_sessions = len(sessions)
    total_duration = sum(s["duration_minutes"] or 0 for s in sessions)
    unique_users = len(set(s["user_name"] for s in sessions))

    by_computer = {}
    for s in sessions:
        comp = f"PC {s['comp_number']}"
        by_computer.setdefault(comp, {"count": 0, "duration": 0})
        by_computer[comp]["count"] += 1
        by_computer[comp]["duration"] += s["duration_minutes"] or 0

    by_user = {}
    for s in sessions:
        name = s["user_name"]
        by_user.setdefault(name, {"count": 0, "duration": 0, "class": s["user_class"] or "Staff"})
        by_user[name]["count"] += 1
        by_user[name]["duration"] += s["duration_minutes"] or 0

    by_date = {}
    for s in sessions:
        d = s["check_in_time"][:10]
        by_date.setdefault(d, {"count": 0, "duration": 0})
        by_date[d]["count"] += 1
        by_date[d]["duration"] += s["duration_minutes"] or 0

    return render_template(
        "reports.html",
        sessions=sessions,
        period=period,
        date_from=date_from,
        date_to=date_to,
        total_sessions=total_sessions,
        total_duration=round(total_duration, 1),
        unique_users=unique_users,
        by_computer=by_computer,
        by_user=by_user,
        by_date=by_date,
    )


@app.route("/export/excel")
def export_excel():
    db = get_db()
    date_from = request.args.get("date_from", "2000-01-01")
    date_to = request.args.get("date_to", "2099-12-31")

    sessions = db.execute("""
        SELECT s.user_name, s.user_class, c.number, s.check_in_time,
               s.check_out_time, s.duration_minutes
        FROM sessions s
        JOIN computers c ON s.computer_id = c.id
        WHERE s.check_in_time >= ? AND s.check_in_time <= ?
        ORDER BY s.check_in_time DESC
    """, (date_from + "T00:00:00", date_to + "T23:59:59")).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Lab Usage Log"

    headers = ["Name", "Class", "Computer", "Check In", "Check Out", "Duration (min)"]
    ws.append(headers)

    for s in sessions:
        ws.append([
            s["user_name"],
            s["user_class"] or "Staff",
            f"PC {s['number']}",
            s["check_in_time"].replace("T", " "),
            (s["check_out_time"] or "").replace("T", " "),
            round(s["duration_minutes"] or 0, 1),
        ])

    ws.auto_filter.ref = ws.dimensions

    filepath = os.path.join(app.root_path, "export.xlsx")
    wb.save(filepath)
    return send_file(filepath, as_attachment=True, download_name="lab_usage.xlsx")


@app.route("/export/pdf")
def export_pdf():
    db = get_db()
    date_from = request.args.get("date_from", "2000-01-01")
    date_to = request.args.get("date_to", "2099-12-31")

    sessions = db.execute("""
        SELECT s.user_name, s.user_class, c.number, s.check_in_time,
               s.check_out_time, s.duration_minutes
        FROM sessions s
        JOIN computers c ON s.computer_id = c.id
        WHERE s.check_in_time >= ? AND s.check_in_time <= ?
        ORDER BY s.check_in_time DESC
    """, (date_from + "T00:00:00", date_to + "T23:59:59")).fetchall()

    filepath = os.path.join(app.root_path, "report.pdf")
    doc = SimpleDocTemplate(filepath, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("Computer Lab Usage Report", styles["Title"]))
    elements.append(Paragraph(
        f"Period: {date_from} to {date_to}", styles["Normal"]
    ))
    elements.append(Spacer(1, 10 * mm))

    data = [["Name", "Class", "PC", "Check In", "Check Out", "Duration (min)"]]
    for s in sessions:
        data.append([
            s["user_name"],
            s["user_class"] or "Staff",
            str(s["number"]),
            (s["check_in_time"] or "")[:16].replace("T", " "),
            (s["check_out_time"] or "Active")[:16].replace("T", " "),
            f"{(s['duration_minutes'] or 0):.1f}",
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)

    doc.build(elements)
    return send_file(filepath, as_attachment=True, download_name="lab_usage.pdf")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))
        flash("Incorrect password.", "error")
    return render_template("login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    computers = db.execute(
        "SELECT * FROM computers ORDER BY number"
    ).fetchall()

    active_count = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE check_out_time IS NULL"
    ).fetchone()[0]

    return render_template(
        "admin.html",
        computers=computers,
        active_count=active_count,
        idle_timeout=IDLE_TIMEOUT_MINUTES,
    )


@app.route("/admin/computer/<int:comp_id>/status", methods=["POST"])
@admin_required
def set_computer_status(comp_id):
    db = get_db()
    status = request.form.get("status", "free")

    if status == "free":
        occupied = db.execute(
            "SELECT id FROM sessions WHERE computer_id = ? AND check_out_time IS NULL",
            (comp_id,),
        ).fetchone()
        if occupied:
            flash("Cannot set to free: there is an active session on this computer.", "error")
            return redirect(url_for("admin"))

    db.execute(
        "UPDATE computers SET status = ? WHERE id = ?", (status, comp_id)
    )
    db.commit()
    flash(f"Computer status updated to {status}.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/session/<int:session_id>/delete", methods=["POST"])
@admin_required
def delete_session(session_id):
    db = get_db()
    sess = db.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if sess:
        if not sess["check_out_time"]:
            db.execute(
                "UPDATE computers SET status = 'free' WHERE id = ?",
                (sess["computer_id"],),
            )
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        db.commit()
        flash("Log entry deleted.", "success")
    return redirect(url_for("history"))


@app.route("/admin/session/<int:session_id>/edit", methods=["POST"])
@admin_required
def edit_session(session_id):
    db = get_db()
    user_name = request.form.get("user_name", "").strip()
    user_class = request.form.get("user_class", "").strip() or None

    if not user_name:
        flash("Name cannot be empty.", "error")
        return redirect(url_for("history"))

    db.execute(
        "UPDATE sessions SET user_name = ?, user_class = ? WHERE id = ?",
        (user_name, user_class, session_id),
    )
    db.commit()
    flash("Log entry updated.", "success")
    return redirect(url_for("history"))


@app.route("/admin/add-computers", methods=["POST"])
@admin_required
def add_computers():
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM computers").fetchone()[0]
    how_many = int(request.form.get("how_many", 5))

    for i in range(count + 1, count + how_many + 1):
        db.execute(
            "INSERT INTO computers (number, label, status) VALUES (?, ?, 'free')",
            (i, f"PC {i}"),
        )
    db.commit()
    flash(f"Added {how_many} new computer(s).", "success")
    return redirect(url_for("admin"))


@app.route("/admin/delete-computer/<int:comp_id>", methods=["POST"])
@admin_required
def delete_computer(comp_id):
    db = get_db()
    active = db.execute(
        "SELECT id FROM sessions WHERE computer_id = ? AND check_out_time IS NULL",
        (comp_id,),
    ).fetchone()
    if active:
        flash("Cannot delete: computer has an active session.", "error")
        return redirect(url_for("admin"))

    db.execute("DELETE FROM computers WHERE id = ?", (comp_id,))
    db.commit()
    flash("Computer removed.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/config", methods=["POST"])
@admin_required
def update_config():
    global IDLE_TIMEOUT_MINUTES
    new_timeout = request.form.get("idle_timeout", IDLE_TIMEOUT_MINUTES)
    IDLE_TIMEOUT_MINUTES = int(new_timeout)
    flash(f"Idle timeout set to {IDLE_TIMEOUT_MINUTES} minutes.", "success")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("LAB_PORT", "80"))
    app.run(host="0.0.0.0", port=port, debug=False)
