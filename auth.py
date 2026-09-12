import sqlite3
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

from database.db import get_db
from extensions import limiter
from security import validate_password, describe_problems

bp = Blueprint("auth", __name__)


def _normalize_name(first_name, last_name):
    """Combine first and last name and title-case them for display."""
    return " ".join(part.title() for part in (first_name, last_name) if part).strip()


def _lookup_by_lrn(db, lrn):
    """Find a pre-registered student by LRN (stored as username) or its
    generated placeholder email. Returns the row or None."""
    return db.execute(
        "SELECT * FROM users WHERE (username = ? OR email = ?) AND role = 'student'",
        (lrn, f"{lrn}@cybersafe.local"),
    ).fetchone()


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
    """First-time password creation now happens through the two-step /login flow.
    This URL is kept for compatibility but redirects there."""
    return redirect(url_for("auth.login"))


def _login_limit():
    if request.method == "POST" and request.form.get("step") == "2":
        return "5 per minute"
    return "10 per minute"


def _login_key():
    if request.method == "POST" and request.form.get("step") == "2":
        return (session.get("login_lrn") or request.remote_addr or "").lower()
    return request.remote_addr or ""


@bp.route("/login", methods=("GET", "POST"))
@limiter.limit(_login_limit, key_func=_login_key)
def login():
    if session.get("user_id"):
        return redirect(url_for("main.dashboard"))

    db = get_db()
    step = request.form.get("step", "1")

    # Step 1: only the LRN is entered.
    if request.method == "POST" and step == "1":
        identifier = request.form.get("identifier", "").strip().lower()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (identifier, identifier),
        ).fetchone()

        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if not identifier:
            error = "Enter your LRN."
            if is_xhr:
                return jsonify({"ok": False, "error": error}), 400
            flash(error, "error")
        elif user is None:
            error = "LRN not found. Ask your administrator to register your account."
            if is_xhr:
                return jsonify({"ok": False, "error": error}), 404
            flash(error, "error")
        elif not user["is_active"]:
            error = "This account has been suspended. Contact an administrator."
            if is_xhr:
                return jsonify({"ok": False, "error": error}), 403
            flash(error, "error")
        else:
            session["login_lrn"] = identifier
            session["login_user_id"] = user["id"]
            session["login_mode"] = "login" if user["is_password_set"] else "create"
            if is_xhr:
                return jsonify({
                    "ok": True,
                    "mode": session["login_mode"],
                    "full_name": user["full_name"],
                    "username": user["username"],
                    "email": user["email"],
                })
            return render_template(
                "login.html",
                step=2,
                mode=session["login_mode"],
                login_user=user,
                identifier=identifier,
            )

    # Step 2: password entry or creation.
    if request.method == "POST" and step == "2":
        identifier = session.get("login_lrn")
        user_id = session.get("login_user_id")
        mode = session.get("login_mode")
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        def step2_error(message, hint=None):
            if is_xhr:
                payload = {"ok": False, "error": message}
                if hint:
                    payload["hint"] = hint
                return jsonify(payload), 400
            flash(message, "error")
            return render_template(
                "login.html",
                step=2,
                mode=mode,
                login_user=user,
                identifier=identifier,
                step2_hint=hint,
            )

        def step2_success():
            session.clear()
            session["user_id"] = user["id"]
            target = (
                url_for("admin.dashboard", welcome=1)
                if user["role"] == "admin"
                else url_for("main.dashboard", welcome=1)
            )
            if is_xhr:
                return jsonify({"ok": True, "redirect": target})
            return redirect(target)

        if not identifier or not user_id or not mode:
            if is_xhr:
                return jsonify({"ok": False, "error": "Session expired. Enter your LRN again."}), 400
            return redirect(url_for("auth.login"))

        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None or user["username"].lower() != identifier.lower():
            session.pop("login_lrn", None)
            session.pop("login_user_id", None)
            session.pop("login_mode", None)
            if is_xhr:
                return jsonify({"ok": False, "error": "Session expired. Enter your LRN again."}), 400
            flash("Session expired. Enter your LRN again.", "error")
            return redirect(url_for("auth.login"))

        if mode == "create":
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if password != confirm_password:
                return step2_error("Passwords do not match.")

            error = describe_problems(
                validate_password(
                    password,
                    personal_values=(user["full_name"], user["username"], user["email"]),
                )
            )
            if error:
                return step2_error(error)

            db.execute(
                "UPDATE users SET password_hash = ?, is_password_set = 1 WHERE id = ?",
                (generate_password_hash(password), user["id"]),
            )
            db.commit()
            return step2_success()

        # mode == "login"
        password = request.form.get("password", "")
        if not check_password_hash(user["password_hash"], password):
            return step2_error("Incorrect password.")

        # A redeemed voucher grants real internet access to the IP that
        # activated it (vouchers.ip_address). Refuse logins from a different
        # IP while that voucher is still active, so one account can't be
        # used on a second device. The same IP reconnecting (page refresh,
        # browser restart, app service restart) is allowed through.
        #
        # KNOWN LIMITATION: this gating is IP-based, not device-based —
        # hash:mac isn't available on this Pi's kernel, see network_access.py.
        # If a student's device gets a new IP from DHCP (e.g. reconnecting to
        # WiFi after a while) while their voucher is still active under the
        # old IP, their own reconnect is treated as "another device" and
        # blocked until the voucher expires. Accepted tradeoff of the current
        # architecture.
        import levels  # deferred: levels imports auth at module load
        active_ip = levels._active_voucher_ip(db, user["id"])
        if active_ip and active_ip != request.remote_addr:
            return step2_error(
                "This account currently has an active internet connection on another device. "
                "Please wait for it to expire, or log in from that device instead.",
                hint="Internet access vouchers are tied to one device at a time.",
            )

        return step2_success()

    # Fresh request: clear any stale step-2 session and show LRN field.
    session.pop("login_lrn", None)
    session.pop("login_user_id", None)
    session.pop("login_mode", None)
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
