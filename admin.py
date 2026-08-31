import os
import sqlite3
from functools import wraps

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from auth import login_required
from database.db import get_db
from ranks import BADGE_TITLES, BADGE_ORDER, rank_info

bp = Blueprint("admin", __name__, url_prefix="/admin")
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


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
            name = request.form.get("grade_name", "").strip()
            if not name:
                flash("Enter a grade name.", "error")
            else:
                try:
                    db.execute("INSERT INTO grades (name) VALUES (?)", (name,))
                    db.commit()
                except sqlite3.IntegrityError:
                    flash("That grade already exists.", "error")
        elif action == "add_section":
            grade_id = request.form.get("grade_id", type=int)
            name = request.form.get("section_name", "").strip()
            if not grade_id or not name:
                flash("Enter a section name.", "error")
            else:
                try:
                    db.execute("INSERT INTO sections (grade_id, name) VALUES (?, ?)", (grade_id, name))
                    db.commit()
                except sqlite3.IntegrityError:
                    flash("That section already exists for this grade.", "error")
        elif action == "delete_grade":
            db.execute("DELETE FROM grades WHERE id = ?", (request.form.get("grade_id", type=int),))
            db.commit()
        elif action == "delete_section":
            db.execute("DELETE FROM sections WHERE id = ?", (request.form.get("section_id", type=int),))
            db.commit()
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
        return redirect(url_for("admin.dashboard"))

    settings = db.execute("SELECT * FROM school_settings WHERE id = 1").fetchone()
    grades = db.execute(
        "SELECT id, name FROM grades ORDER BY name COLLATE NOCASE"
    ).fetchall()
    sections = db.execute(
        "SELECT id, grade_id, name FROM sections ORDER BY grade_id, name COLLATE NOCASE"
    ).fetchall()

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

    return render_template(
        "admin.html",
        settings=settings,
        grades=grades,
        sections=sections,
        mission_levels=mission_levels,
        total_questions=total_questions,
        rankings=paginated_rankings,
        pagination=pagination,
        badges=BADGE_TITLES,
        badge_filter=badge_filter,
        q=q,
    )