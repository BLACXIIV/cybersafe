from flask import Blueprint, render_template, g

from auth import login_required
from database.db import get_db

bp = Blueprint("main", __name__)


def _mission_summary():
    """Small real-data summary for the dashboard: per-level status + overall progress."""
    db = get_db()
    user_id = g.user["id"]

    all_levels = db.execute("SELECT * FROM levels ORDER BY level_number").fetchall()
    summaries = []
    total_answered = 0
    total_questions = 0

    for lvl in all_levels:
        progress = db.execute(
            "SELECT * FROM user_level_progress WHERE user_id = ? AND level_id = ?",
            (user_id, lvl["id"]),
        ).fetchone()
        q_count = db.execute(
            "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
        ).fetchone()["c"]
        answered = db.execute(
            """SELECT COUNT(*) AS c FROM user_answers ua
               JOIN questions q ON q.id = ua.question_id
               WHERE ua.user_id = ? AND q.level_id = ?""",
            (user_id, lvl["id"]),
        ).fetchone()["c"]

        total_answered += answered
        total_questions += q_count

        summaries.append({
            "level": lvl,
            "status": progress["status"] if progress else "not_started",
        })

    overall_pct = round((total_answered / total_questions) * 100) if total_questions else 0
    return summaries, overall_pct


@bp.route("/")
def landing():
    if g.user:
        summaries, overall_pct = _mission_summary()
        return render_template("dashboard.html", user=g.user, level_summaries=summaries, overall_pct=overall_pct)
    return render_template("index.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    summaries, overall_pct = _mission_summary()
    return render_template("dashboard.html", user=g.user, level_summaries=summaries, overall_pct=overall_pct)
