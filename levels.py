from flask import Blueprint, render_template, redirect, url_for, request, g, flash

from auth import login_required
from database.db import get_db

bp = Blueprint("levels", __name__, url_prefix="/levels")

QUESTIONS_PER_LEVEL_MAX_POINTS = 100  # each question's best choice is worth 100


def _get_or_create_progress(db, user_id, level_id):
    row = db.execute(
        "SELECT * FROM user_level_progress WHERE user_id = ? AND level_id = ?",
        (user_id, level_id),
    ).fetchone()
    if row is None:
        db.execute(
            """INSERT INTO user_level_progress (user_id, level_id, status, total_points)
               VALUES (?, ?, 'in_progress', 0)""",
            (user_id, level_id),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM user_level_progress WHERE user_id = ? AND level_id = ?",
            (user_id, level_id),
        ).fetchone()
    return row


def _is_level_unlocked(db, user_id, level_number):
    """Level 1 is always unlocked. Level N unlocks once level N-1 is completed."""
    if level_number == 1:
        return True
    prev = db.execute(
        """SELECT ulp.status FROM user_level_progress ulp
           JOIN levels l ON l.id = ulp.level_id
           WHERE ulp.user_id = ? AND l.level_number = ?""",
        (user_id, level_number - 1),
    ).fetchone()
    return prev is not None and prev["status"] == "completed"


@bp.route("/")
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]
    all_levels = db.execute("SELECT * FROM levels ORDER BY level_number").fetchall()

    level_summaries = []
    for lvl in all_levels:
        progress = db.execute(
            "SELECT * FROM user_level_progress WHERE user_id = ? AND level_id = ?",
            (user_id, lvl["id"]),
        ).fetchone()
        total_questions = db.execute(
            "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
        ).fetchone()["c"]
        answered = db.execute(
            """SELECT COUNT(*) AS c FROM user_answers ua
               JOIN questions q ON q.id = ua.question_id
               WHERE ua.user_id = ? AND q.level_id = ?""",
            (user_id, lvl["id"]),
        ).fetchone()["c"]

        level_summaries.append({
            "level": lvl,
            "status": progress["status"] if progress else "not_started",
            "total_points": progress["total_points"] if progress else 0,
            "max_points": total_questions * QUESTIONS_PER_LEVEL_MAX_POINTS,
            "answered": answered,
            "total_questions": total_questions,
            "unlocked": _is_level_unlocked(db, user_id, lvl["level_number"]),
        })

    return render_template("levels.html", level_summaries=level_summaries)


@bp.route("/<int:level_number>/play")
@login_required
def play(level_number):
    db = get_db()
    user_id = g.user["id"]

    lvl = db.execute("SELECT * FROM levels WHERE level_number = ?", (level_number,)).fetchone()
    if lvl is None:
        flash("That level doesn't exist.", "error")
        return redirect(url_for("levels.index"))

    if not _is_level_unlocked(db, user_id, level_number):
        flash("Complete the previous level first to unlock this one.", "error")
        return redirect(url_for("levels.index"))

    _get_or_create_progress(db, user_id, lvl["id"])

    # One attempt per question: find the first question in this level
    # the student hasn't answered yet, in question_number order.
    next_question = db.execute(
        """SELECT q.* FROM questions q
           WHERE q.level_id = ?
             AND q.id NOT IN (
                 SELECT question_id FROM user_answers WHERE user_id = ?
             )
           ORDER BY q.question_number ASC
           LIMIT 1""",
        (lvl["id"], user_id),
    ).fetchone()

    if next_question is None:
        return redirect(url_for("levels.results", level_number=level_number))

    choices = db.execute(
        "SELECT * FROM choices WHERE question_id = ? ORDER BY letter ASC",
        (next_question["id"],),
    ).fetchall()

    total_questions = db.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
    ).fetchone()["c"]

    return render_template(
        "question.html",
        level=lvl,
        question=next_question,
        choices=choices,
        question_position=next_question["question_number"],
        total_questions=total_questions,
    )


@bp.route("/<int:level_number>/answer", methods=("POST",))
@login_required
def answer(level_number):
    db = get_db()
    user_id = g.user["id"]

    lvl = db.execute("SELECT * FROM levels WHERE level_number = ?", (level_number,)).fetchone()
    if lvl is None:
        flash("That level doesn't exist.", "error")
        return redirect(url_for("levels.index"))

    question_id = request.form.get("question_id", type=int)
    choice_id = request.form.get("choice_id", type=int)

    question = db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    choice = db.execute("SELECT * FROM choices WHERE id = ?", (choice_id,)).fetchone()

    if question is None or choice is None or question["level_id"] != lvl["id"]:
        flash("Invalid answer submission.", "error")
        return redirect(url_for("levels.play", level_number=level_number))

    _get_or_create_progress(db, user_id, lvl["id"])

    already_answered = db.execute(
        "SELECT 1 FROM user_answers WHERE user_id = ? AND question_id = ?",
        (user_id, question_id),
    ).fetchone()

    if already_answered is None:
        db.execute(
            """INSERT INTO user_answers (user_id, question_id, choice_id, points_earned)
               VALUES (?, ?, ?, ?)""",
            (user_id, question_id, choice_id, choice["points"]),
        )
        # Keep a running total on both the level-progress row and the user's
        # overall points, since one attempt per question means this answer
        # is final.
        db.execute(
            """UPDATE user_level_progress SET total_points = total_points + ?
               WHERE user_id = ? AND level_id = ?""",
            (choice["points"], user_id, lvl["id"]),
        )
        db.execute(
            "UPDATE users SET points = points + ? WHERE id = ?",
            (choice["points"], user_id),
        )
        db.commit()

    return redirect(url_for("levels.play", level_number=level_number))


@bp.route("/<int:level_number>/results")
@login_required
def results(level_number):
    db = get_db()
    user_id = g.user["id"]

    lvl = db.execute("SELECT * FROM levels WHERE level_number = ?", (level_number,)).fetchone()
    if lvl is None:
        flash("That level doesn't exist.", "error")
        return redirect(url_for("levels.index"))

    total_questions = db.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
    ).fetchone()["c"]
    answered = db.execute(
        """SELECT COUNT(*) AS c FROM user_answers ua
           JOIN questions q ON q.id = ua.question_id
           WHERE ua.user_id = ? AND q.level_id = ?""",
        (user_id, lvl["id"]),
    ).fetchone()["c"]

    if answered < total_questions:
        # Not actually finished yet — send back to the next question.
        return redirect(url_for("levels.play", level_number=level_number))

    progress = _get_or_create_progress(db, user_id, lvl["id"])

    if progress["status"] != "completed":
        db.execute(
            "UPDATE user_level_progress SET status = 'completed' WHERE user_id = ? AND level_id = ?",
            (user_id, lvl["id"]),
        )
        db.commit()
        progress = db.execute(
            "SELECT * FROM user_level_progress WHERE user_id = ? AND level_id = ?",
            (user_id, lvl["id"]),
        ).fetchone()

    max_points = total_questions * QUESTIONS_PER_LEVEL_MAX_POINTS
    percentage = round((progress["total_points"] / max_points) * 100) if max_points else 0

    # Per-question breakdown for review.
    rows = db.execute(
        """SELECT q.question_number, q.prompt,
                  c.letter AS chosen_letter, c.choice_text AS chosen_text, c.points,
                  best.letter AS best_letter, best.choice_text AS best_text
           FROM user_answers ua
           JOIN questions q ON q.id = ua.question_id
           JOIN choices c ON c.id = ua.choice_id
           JOIN choices best ON best.question_id = q.id AND best.points = 100
           WHERE ua.user_id = ? AND q.level_id = ?
           ORDER BY q.question_number ASC""",
        (user_id, lvl["id"]),
    ).fetchall()

    next_level = db.execute(
        "SELECT * FROM levels WHERE level_number = ?", (level_number + 1,)
    ).fetchone()

    return render_template(
        "results.html",
        level=lvl,
        total_points=progress["total_points"],
        max_points=max_points,
        percentage=percentage,
        rows=rows,
        next_level=next_level,
    )
