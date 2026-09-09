import os
import re
import sqlite3
from functools import wraps
from io import BytesIO

import openpyxl
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from werkzeug.security import generate_password_hash

from auth import login_required, _lookup_by_lrn, _normalize_name
from database.db import get_db
from extensions import limiter
from ranks import BADGE_TITLES, BADGE_ORDER, rank_info
from security import validate_password, describe_problems

bp = Blueprint("admin", __name__, url_prefix="/admin")
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def _normalize_grade_name(name):
    """Convert 'Grade 10', '10', 'G10' into 'Grade 10'. Otherwise strip and return as-is."""
    if not name:
        return ""
    name = name.strip()
    digits = ""
    for i, ch in enumerate(name):
        if ch.isdigit():
            digits += ch
        elif digits and not ch.isspace():
            break
    if digits:
        return f"Grade {digits}"
    return name


def _get_or_create_grade(db, grade_name):
    """Return the id of a grade level, creating it if it does not exist."""
    normalized = _normalize_grade_name(grade_name)
    row = db.execute(
        "SELECT id FROM grades WHERE name = ? OR name = ?", (grade_name, normalized)
    ).fetchone()
    if row:
        return row["id"]
    return db.execute("INSERT INTO grades (name) VALUES (?)", (normalized,)).lastrowid


def _insert_student(db, lrn, first_name, last_name, grade_name):
    """Insert a pre-registered student (no password yet). Returns the new id."""
    db.execute(
        """INSERT INTO users
           (full_name, username, email, password_hash, grade_section, role, is_password_set, is_active)
           VALUES (?, ?, ?, '', ?, 'student', 0, 1)""",
        (_normalize_name(first_name, last_name), lrn, f"{lrn}@cybersafe.local", grade_name),
    )
    return db.execute("SELECT id FROM users WHERE username = ?", (lrn,)).fetchone()["id"]


def _import_students_from_excel(db, file_storage):
    """Parse an uploaded .xlsx with columns: LRN, First Name, Last Name, Grade Level.
    Returns (added, skipped, errors)."""
    added, skipped, errors = 0, 0, []

    try:
        wb = openpyxl.load_workbook(BytesIO(file_storage.read()))
        ws = wb.active

        # The provided file has a multi-line title in row 1 and headers in row 2.
        # Try to find the header row.
        header_row = None
        headers = []
        for r in range(1, min(ws.max_row, 10) + 1):
            values = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
            lowered = [str(v).lower().strip() if v is not None else "" for v in values]
            if "learner reference number" in lowered or ("first name" in lowered and "last name" in lowered):
                header_row = r
                headers = values
                break

        if header_row is None:
            return 0, 0, ["Could not find a header row with 'Learner Reference Number', 'First Name', 'Last Name', 'Grade Level'."]

        # Map header names to column indexes.
        header_map = {str(h).lower().strip(): i for i, h in enumerate(headers)}

        def col(name):
            return header_map.get(name)

        lrn_idx = col("learner reference number")
        first_idx = col("first name")
        last_idx = col("last name")
        grade_idx = col("grade level")

        if lrn_idx is None or first_idx is None or last_idx is None or grade_idx is None:
            return 0, 0, ["Required columns not found: Learner Reference Number, First Name, Last Name, Grade Level."]

        for r in range(header_row + 1, ws.max_row + 1):
            lrn = ws.cell(row=r, column=lrn_idx + 1).value
            if lrn is None:
                continue
            lrn = str(lrn).strip()
            if not lrn:
                continue

            first_name = str(ws.cell(row=r, column=first_idx + 1).value or "").strip()
            last_name = str(ws.cell(row=r, column=last_idx + 1).value or "").strip()
            grade_name = str(ws.cell(row=r, column=grade_idx + 1).value or "").strip()

            if not first_name or not last_name or not grade_name:
                errors.append(f"Row {r}: missing first name, last name, or grade.")
                continue

            existing = _lookup_by_lrn(db, lrn)
            if existing:
                skipped += 1
                continue

            try:
                _get_or_create_grade(db, grade_name)
                _insert_student(db, lrn, first_name, last_name, grade_name)
                added += 1
            except sqlite3.IntegrityError as e:
                errors.append(f"Row {r}: could not add {lrn} ({e}).")

    except Exception as e:
        errors.append(f"Could not read Excel file: {e}")

    return added, skipped, errors


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if g.user["role"] != "admin":
            flash("Administrator access is required.", "error")
            return redirect(url_for("main.dashboard"))
        return view(*args, **kwargs)
    return wrapped_view


@bp.route("/", methods=("GET", "POST"))
@admin_required
@limiter.limit("60 per minute")
def dashboard():
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "branding":
            school_name = request.form.get("school_name", "").strip()
            if not school_name:
                flash("School name is required.", "error")
            else:
                logo = request.files.get("school_logo")
                logo_path = db.execute("SELECT logo_path FROM school_settings WHERE id = 1").fetchone()["logo_path"]
                if logo and logo.filename:
                    extension = logo.filename.rsplit(".", 1)[-1].lower() if "." in logo.filename else ""
                    if extension not in ALLOWED_LOGO_EXTENSIONS:
                        flash("Logo must be PNG, JPG, WEBP, or GIF.", "error")
                        return redirect(url_for("admin.dashboard"))
                    filename = f"school-logo.{extension}"
                    upload_dir = os.path.join(current_app.static_folder, "uploads")
                    os.makedirs(upload_dir, exist_ok=True)
                    logo.save(os.path.join(upload_dir, secure_filename(filename)))
                    logo_path = f"uploads/{filename}"
                db.execute(
                    "UPDATE school_settings SET school_name = ?, logo_path = ? WHERE id = 1",
                    (school_name, logo_path),
                )
                db.commit()
        elif action == "add_grade":
            name = _normalize_grade_name(request.form.get("grade_name", ""))
            if not name:
                flash("Enter a grade name.", "error")
            else:
                try:
                    db.execute("INSERT INTO grades (name) VALUES (?)", (name,))
                    db.commit()
                except sqlite3.IntegrityError:
                    flash("That grade already exists.", "error")
        elif action == "delete_grade":
            db.execute("DELETE FROM grades WHERE id = ?", (request.form.get("grade_id", type=int),))
            db.commit()
        elif action == "add_student":
            lrn = request.form.get("lrn", "").strip()
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            grade_name = request.form.get("grade_name", "").strip()

            error = None
            if not lrn:
                error = "LRN is required."
            elif not first_name or not last_name:
                error = "First and last name are required."
            elif not grade_name:
                error = "Grade level is required."
            elif _lookup_by_lrn(db, lrn):
                error = "That LRN is already registered."

            if error is None:
                try:
                    _get_or_create_grade(db, grade_name)
                    _insert_student(db, lrn, first_name, last_name, grade_name)
                    db.commit()
                    flash(f"Registered {first_name} {last_name}.", "success")
                except sqlite3.IntegrityError:
                    flash("That LRN is already registered.", "error")
            else:
                flash(error, "error")
        elif action == "import_students":
            file = request.files.get("student_file")
            if not file or not file.filename:
                flash("Choose an Excel file to import.", "error")
            else:
                added, skipped, errors = _import_students_from_excel(db, file)
                db.commit()
                if added:
                    flash(f"Imported {added} student(s).", "success")
                if skipped:
                    flash(f"Skipped {skipped} duplicate(s).", "info")
                for error in errors[:5]:
                    flash(error, "error")
                if len(errors) > 5:
                    flash(f"...and {len(errors) - 5} more errors.", "error")
        elif action == "toggle_user":
            user_id = request.form.get("user_id", type=int)
            if user_id:
                current = db.execute("SELECT is_active, role FROM users WHERE id = ?", (user_id,)).fetchone()
                if current and current["role"] != "admin":
                    new_state = 0 if current["is_active"] else 1
                    db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, user_id))
                    db.commit()
                else:
                    flash("Cannot suspend an admin account.", "error")
        elif action == "reset_password":
            # Lets an admin hand a forgotten account a new password. The same
            # strength rules as signup apply.
            user_id = request.form.get("user_id", type=int)
            password = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            student = db.execute(
                "SELECT id, full_name, username, email, role FROM users WHERE id = ?", (user_id,)
            ).fetchone() if user_id else None

            if student is None or student["role"] == "admin":
                flash("Cannot change the password for that account.", "error")
            elif password != confirm:
                flash("Passwords do not match.", "error")
            else:
                problem = describe_problems(
                    validate_password(
                        password,
                        personal_values=(student["full_name"], student["username"], student["email"]),
                    )
                )
                if problem:
                    flash(problem, "error")
                else:
                    db.execute(
                        "UPDATE users SET password_hash = ?, is_password_set = 1 WHERE id = ?",
                        (generate_password_hash(password), student["id"]),
                    )
                    db.commit()
                    flash(f"Password updated for {student['full_name']}.", "success")
        return redirect(url_for("admin.dashboard"))

    def _grade_sort_key(g):
        m = re.search(r"(\d+)", g["name"])
        if m:
            return (int(m.group(1)), g["name"].lower())
        return (0, g["name"].lower())

    settings = db.execute("SELECT * FROM school_settings WHERE id = 1").fetchone()
    grades = sorted(
        db.execute("SELECT id, name FROM grades").fetchall(),
        key=_grade_sort_key,
    )

    mission_levels = []
    for lvl in db.execute("SELECT * FROM levels ORDER BY level_number").fetchall():
        items = []
        for question in db.execute(
            "SELECT * FROM questions WHERE level_id = ? ORDER BY question_number", (lvl["id"],)
        ).fetchall():
            choices = db.execute(
                "SELECT * FROM choices WHERE question_id = ? ORDER BY letter", (question["id"],)
            ).fetchall()
            items.append({
                "question": question,
                "choices": choices,
                "correct": next((c for c in choices if c["points"] == 100), None),
            })
        mission_levels.append({"level": lvl, "items": items})

    total_questions = db.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
    max_points = total_questions * 100
    badge_filter = request.args.get("badge", "")
    q = request.args.get("q", "").strip().lower()
    page = request.args.get("page", 1, type=int)
    per_page = 10

    students = db.execute(
        """SELECT id, full_name, username, grade_section, points, level, is_active
           FROM users WHERE role = 'student' ORDER BY points DESC, full_name COLLATE NOCASE ASC"""
    ).fetchall()

    rankings = []
    for s in students:
        current_badge, current_title, _, _, _, _ = rank_info(s["points"], max_points)
        row = dict(s)
        row["current_badge"] = current_badge
        row["current_title"] = current_title
        rankings.append(row)

    # Sort: highest badge first, then highest score.
    rankings.sort(key=lambda r: (-BADGE_ORDER[r["current_badge"]], -r["points"]))

    if badge_filter in BADGE_ORDER:
        rankings = [r for r in rankings if r["current_badge"] == badge_filter]

    if q:
        rankings = [
            r for r in rankings
            if q in r["full_name"].lower()
            or q in r["username"].lower()
            or (r["grade_section"] and q in r["grade_section"].lower())
            or q in r["current_title"].lower()
        ]

    total = len(rankings)
    total_pages = (total + per_page - 1) // per_page if total else 1
    page = max(1, page)
    if page > total_pages and total:
        page = total_pages
    start = (page - 1) * per_page
    paginated_rankings = rankings[start : start + per_page]

    def page_url(page_num):
        args = {}
        if badge_filter:
            args["badge"] = badge_filter
        if q:
            args["q"] = q
        if page_num != 1:
            args["page"] = page_num
        return url_for("admin.dashboard", **args)

    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < total_pages else None,
        "start_rank": start,
    }

    # Quick-view susceptibility breakdown for the dashboard card.
    all_students = db.execute(
        """SELECT id, full_name, username, grade_section, points, level, is_active
           FROM users WHERE role = 'student'"""
    ).fetchall()
    sus_count = 0
    non_count = 0
    for s in all_students:
        current_badge, _, _, _, _, _ = rank_info(s["points"], max_points)
        if current_badge in ("Bronze", "Silver"):
            sus_count += 1
        else:
            non_count += 1
    total_students = sus_count + non_count
    if total_students:
        sus_pct = round((sus_count / total_students) * 100, 1)
        non_pct = round(100 - sus_pct, 1)
    else:
        sus_pct = non_pct = 0

    return render_template(
        "admin.html",
        settings=settings,
        grades=grades,
        mission_levels=mission_levels,
        total_questions=total_questions,
        rankings=paginated_rankings,
        pagination=pagination,
        badges=BADGE_TITLES,
        badge_filter=badge_filter,
        q=q,
        sus_count=sus_count,
        non_count=non_count,
        total_students=total_students,
        sus_pct=sus_pct,
        non_pct=non_pct,
    )


@bp.route("/download-student-template")
@admin_required
@limiter.limit("10 per minute")
def download_student_template():
    """Return an empty .xlsx template with the columns required for bulk import."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "LearnerDataEntry"
    headers = ["Learner Reference Number", "First Name", "Last Name", "Grade Level"]
    ws.append(headers)
    for col, header in enumerate(headers, start=1):
        ws.cell(row=1, column=col).font = openpyxl.styles.Font(bold=True)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="cybersafe-student-import-template.xlsx",
    )


@bp.route("/report")
@admin_required
@limiter.limit("60 per minute")
def report():
    """Show a susceptibility report: students below Gold and a pie chart."""
    db = get_db()
    total_questions = db.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
    max_points = total_questions * 100

    students = db.execute(
        """SELECT id, full_name, username, grade_section, points, level, is_active
           FROM users WHERE role = 'student'
           ORDER BY points ASC, full_name COLLATE NOCASE ASC"""
    ).fetchall()

    susceptible = []
    non_count = 0
    for s in students:
        current_badge, current_title, _, _, _, _ = rank_info(s["points"], max_points)
        row = dict(s)
        row["current_badge"] = current_badge
        row["current_title"] = current_title
        if current_badge in ("Bronze", "Silver"):
            susceptible.append(row)
        else:
            non_count += 1

    total = len(students)
    sus_count = len(susceptible)
    if total:
        sus_pct = round((sus_count / total) * 100, 1)
        non_pct = round(100 - sus_pct, 1)
    else:
        sus_pct = non_pct = 0

    badge_filter = request.args.get("badge", "")
    q = request.args.get("q", "").strip().lower()
    page = request.args.get("page", 1, type=int)
    per_page = 10

    if badge_filter in BADGE_ORDER:
        susceptible = [r for r in susceptible if r["current_badge"] == badge_filter]

    if q:
        susceptible = [
            r for r in susceptible
            if q in r["full_name"].lower()
            or q in r["username"].lower()
            or (r["grade_section"] and q in r["grade_section"].lower())
            or q in r["current_title"].lower()
        ]

    filtered_total = len(susceptible)
    total_pages = (filtered_total + per_page - 1) // per_page if filtered_total else 1
    page = max(1, page)
    if page > total_pages and filtered_total:
        page = total_pages
    start = (page - 1) * per_page
    paginated = susceptible[start : start + per_page]

    def page_url(page_num):
        args = {}
        if badge_filter:
            args["badge"] = badge_filter
        if q:
            args["q"] = q
        if page_num != 1:
            args["page"] = page_num
        return url_for("admin.report", **args)

    pagination = {
        "page": page,
        "per_page": per_page,
        "total": filtered_total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < total_pages else None,
        "start_rank": start,
    }

    return render_template(
        "admin_report.html",
        susceptible=paginated,
        non_count=non_count,
        sus_count=sus_count,
        total=total,
        sus_pct=sus_pct,
        non_pct=non_pct,
        pagination=pagination,
        q=q,
        badge_filter=badge_filter,
        badges=BADGE_TITLES,
    )