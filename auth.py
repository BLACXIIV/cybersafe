import sqlite3
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, g
)
from werkzeug.security import generate_password_hash, check_password_hash

from database.db import get_db
from security import validate_password, describe_problems

bp = Blueprint("auth", __name__)


def login_required(view):
    """Decorator: redirect to login if no user is in the session."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            session.clear()
            flash("Please log in to continue.", "error")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped_view


def student_required(view):
    """Decorator: redirect to admin dashboard if the logged-in user is an admin.

    Use on student-only routes (dashboard, exams, internet access, etc.).
    """
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if g.user["role"] == "admin":
            flash("This area is for students only.", "error")
            return redirect(url_for("admin.dashboard"))
        return view(*args, **kwargs)
    return wrapped_view


@bp.route("/signup", methods=("GET", "POST"))
def signup():
    if session.get("user_id"):
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        grade_id = request.form.get("grade_id", type=int)
        section_id = request.form.get("section_id", type=int)
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        error = None
        if not full_name:
            error = "Full name is required."
        elif not username:
            error = "Username is required."
        elif not email or "@" not in email:
            error = "A valid email is required."
        elif not grade_id or not section_id:
            error = "Select a grade and section."
        elif password != confirm_password:
            error = "Passwords do not match."
        else:
            # The browser shows the same rules live; this is the authoritative check.
            error = describe_problems(
                validate_password(password, personal_values=(full_name, username, email))
            )

        if error is None:
            db = get_db()
            selected_section = db.execute(
                """SELECT g.name AS grade_name, s.name AS section_name
                   FROM sections s JOIN grades g ON g.id = s.grade_id
                   WHERE s.id = ? AND s.grade_id = ?""",
                (section_id, grade_id),
            ).fetchone()
            if selected_section is None:
                error = "Select a valid section for the chosen grade."
            else:
                grade_section = f"{selected_section['grade_name']} - {selected_section['section_name']}"
            try:
                if error is None:
                    db.execute(
                        """INSERT INTO users
                           (full_name, username, email, password_hash, grade_section)
                           VALUES (?, ?, ?, ?, ?)""",
                        (full_name, username, email,
                         generate_password_hash(password), grade_section),
                    )
                    db.commit()
            except sqlite3.IntegrityError:
                error = "That username or email is already registered."
            if error is None:
                flash("Account created! You can now log in.", "success")
                return redirect(url_for("auth.login"))

        flash(error, "error")

    db = get_db()
    grades = db.execute("SELECT id, name FROM grades ORDER BY name COLLATE NOCASE").fetchall()
    sections = db.execute(
        "SELECT id, grade_id, name FROM sections ORDER BY grade_id, name COLLATE NOCASE"
    ).fetchall()
    return render_template("signup.html", grades=grades, sections=sections)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if session.get("user_id"):
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (identifier, identifier),
        ).fetchone()

        error = None
        if user is None:
            error = "Incorrect username/email or password."
        elif not check_password_hash(user["password_hash"], password):
            error = "Incorrect username/email or password."
        elif not user["is_active"]:
            error = "This account has been suspended. Contact an administrator."

        if error is None:
            session.clear()
            session["user_id"] = user["id"]
            if user["role"] == "admin":
                return redirect(url_for("admin.dashboard", welcome=1))
            return redirect(url_for("main.dashboard", welcome=1))

        flash(error, "error")

    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.landing"))


@bp.before_app_request
def load_logged_in_user():
    """Attach the current user (or None) to flask.g on every request."""
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        user = get_db().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user and not user["is_active"]:
            session.clear()
            g.user = None
        else:
            g.user = user
