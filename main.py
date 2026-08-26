from datetime import datetime

from flask import Blueprint, render_template, g, request, redirect, url_for, flash

import levels
from auth import login_required
from database.db import get_db

bp = Blueprint("main", __name__)


from ranks import BADGE_TITLES, rank_info as _rank_info


def _mission_summary():
    """Small real-data summary for the dashboard: per-level status + overall progress."""
    db = get_db()
    user_id = g.user["id"]

    all_levels = db.execute("SELECT * FROM levels ORDER BY level_number").fetchall()
    summaries = []
    total_questions = 0

    for lvl in all_levels:
        progress = db.execute(
            "SELECT * FROM user_level_progress WHERE user_id = ? AND level_id = ?",
            (user_id, lvl["id"]),
        ).fetchone()
        q_count = db.execute(
            "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
        ).fetchone()["c"]

        total_questions += q_count

        summaries.append({
            "level": lvl,
            "status": progress["status"] if progress else "not_started",
            "unlocked": levels._is_level_unlocked(db, user_id, lvl["level_number"]),
        })

    max_points = total_questions * 100
    points = g.user["points"]
    overall_pct = round((points / max_points) * 100) if max_points else 0

    current_badge, current_title, next_badge, next_title, next_threshold, points_to_next = _rank_info(
        points, max_points
    )

    # Keep the hero title in sync with the badge.
    if g.user["level"] != current_title:
        db.execute("UPDATE users SET level = ? WHERE id = ?", (current_title, user_id))
        db.commit()
        g.user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    all_vouchers = db.execute(
        """SELECT v.*, l.name AS level_name, l.level_number
           FROM vouchers v
           JOIN levels l ON l.id = v.level_id
           WHERE v.user_id = ?
           ORDER BY v.created_at DESC""",
        (user_id,),
    ).fetchall()

    active_voucher = None
    for v in all_vouchers:
        if levels._voucher_active(v):
            active_voucher = v
            break

    rank_info = {
        "points": points,
        "max_points": max_points,
        "pct": max(0, min(100, overall_pct)),
        "current_badge": current_badge,
        "current_title": current_title,
        "next_badge": next_badge,
        "next_title": next_title,
        "next_threshold": next_threshold,
        "points_to_next": points_to_next,
    }

    return summaries, active_voucher, all_vouchers, rank_info


@bp.route("/")
def landing():
    if g.user:
        summaries, active_voucher, all_vouchers, rank_info = _mission_summary()
        return render_template(
            "dashboard.html",
            user=g.user,
            level_summaries=summaries,
            active_voucher=active_voucher,
            all_vouchers=all_vouchers,
            rank_info=rank_info,
        )
    return render_template("index.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    summaries, active_voucher, all_vouchers, rank_info = _mission_summary()
    return render_template(
        "dashboard.html",
        user=g.user,
        level_summaries=summaries,
        active_voucher=active_voucher,
        all_vouchers=all_vouchers,
        rank_info=rank_info,
    )


@bp.route("/internet-access")
@login_required
def internet_access():
    db = get_db()
    user_id = g.user["id"]
    all_vouchers = db.execute(
        """SELECT v.*, l.name AS level_name, l.level_number
           FROM vouchers v
           JOIN levels l ON l.id = v.level_id
           WHERE v.user_id = ?
           ORDER BY v.created_at DESC""",
        (user_id,),
    ).fetchall()

    active_voucher = None
    for v in all_vouchers:
        if levels._voucher_active(v):
            active_voucher = v
            break

    def _remaining(expires_at):
        if not expires_at:
            return None
        try:
            delta = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S") - datetime.utcnow()
        except ValueError:
            return None
        if delta.total_seconds() <= 0:
            return None
        return delta

    return render_template(
        "internet_access.html",
        active_voucher=active_voucher,
        all_vouchers=all_vouchers,
        remaining=_remaining,
        voucher_active=levels._voucher_active,
    )


@bp.route("/internet-access/toggle", methods=("POST",))
@login_required
def internet_access_toggle():
    """Toggle the user's internet access on/off using an available voucher."""
    db = get_db()
    user_id = g.user["id"]

    all_vouchers = db.execute(
        "SELECT * FROM vouchers WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()

    # Find an active voucher and disconnect it.
    for v in all_vouchers:
        if levels._voucher_active(v):
            db.execute(
                "UPDATE vouchers SET used_at = NULL, expires_at = NULL WHERE id = ?",
                (v["id"],),
            )
            db.commit()
            flash("Internet access turned off.", "success")
            return redirect(url_for("main.internet_access"))

    # No active voucher; try to connect an unused one.
    unused = next((v for v in all_vouchers if v["used_at"] is None and v["expires_at"] is None), None)
    if unused is None:
        flash("No voucher available. Complete a mission to earn one.", "error")
        return redirect(url_for("main.internet_access"))

    db.execute(
        "UPDATE vouchers SET used_at = CURRENT_TIMESTAMP, expires_at = datetime('now', '+5 hours') WHERE id = ?",
        (unused["id"],),
    )
    db.commit()
    flash("Internet access turned on.", "success")
    return redirect(url_for("main.internet_access"))
