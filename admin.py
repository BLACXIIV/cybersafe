import os
import sqlite3
from functools import wraps

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from auth import login_required
from database.db import get_db

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
                flash("School branding updated.", "success")
        elif action == "add_grade":
            name = request.form.get("grade_name", "").strip()
            if not name:
                flash("Enter a grade name.", "error")
            else:
                try:
                    db.execute("INSERT INTO grades (name) VALUES (?)", (name,))
                    db.commit()
                    flash("Grade added.", "success")
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
                    flash("Section added.", "success")
                except sqlite3.IntegrityError:
                    flash("That section already exists for this grade.", "error")
        elif action == "delete_grade":
            db.execute("DELETE FROM grades WHERE id = ?", (request.form.get("grade_id", type=int),))
            db.commit()
            flash("Grade removed.", "success")
        elif action == "delete_section":
            db.execute("DELETE FROM sections WHERE id = ?", (request.form.get("section_id", type=int),))
            db.commit()
            flash("Section removed.", "success")
        return redirect(url_for("admin.dashboard"))

    settings = db.execute("SELECT * FROM school_settings WHERE id = 1").fetchone()
    grades = db.execute(
        "SELECT id, name FROM grades ORDER BY name COLLATE NOCASE"
    ).fetchall()
    sections = db.execute(
        "SELECT id, grade_id, name FROM sections ORDER BY grade_id, name COLLATE NOCASE"
    ).fetchall()
    rankings = db.execute(
        """SELECT full_name, username, grade_section, points, level
           FROM users WHERE role = 'student' ORDER BY points DESC, full_name COLLATE NOCASE ASC"""
    ).fetchall()
    return render_template("admin.html", settings=settings, grades=grades, sections=sections, rankings=rankings)