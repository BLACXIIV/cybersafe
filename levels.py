import random
import secrets
import string
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, g, flash, current_app

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


def _last_answer_points(db, user_id, level_id):
    """Return the points_earned from the user's most recent answer in this level."""
    row = db.execute(
        """SELECT ua.points_earned
           FROM user_answers ua
           JOIN questions q ON q.id = ua.question_id
           WHERE ua.user_id = ? AND q.level_id = ?
           ORDER BY ua.answered_at DESC, ua.id DESC
           LIMIT 1""",
        (user_id, level_id),
    ).fetchone()
    return row["points_earned"] if row else None


def _latest_answer_for_question(db, user_id, question_id):
    """Return the most recent answer for a specific question, or None."""
    return db.execute(
        """SELECT * FROM user_answers
           WHERE user_id = ? AND question_id = ?
           ORDER BY answered_at DESC, id DESC
           LIMIT 1""",
        (user_id, question_id),
    ).fetchone()


def _question_best_points(db, user_id, question_id):
    """Return the highest points the user has earned on this question."""
    row = db.execute(
        "SELECT MAX(points_earned) AS best FROM user_answers WHERE user_id = ? AND question_id = ?",
        (user_id, question_id),
    ).fetchone()
    return row["best"] or 0


def _is_perfect(db, user_id, level_id):
    """Return True if the user has earned 100 on every question in the level."""
    return (
        db.execute(
            """SELECT 1 FROM questions q
               WHERE q.level_id = ?
                 AND COALESCE(
                     (SELECT MAX(points_earned)
                      FROM user_answers
                      WHERE user_id = ? AND question_id = q.id),
                     0
                 ) < 100
               LIMIT 1""",
            (level_id, user_id),
        ).fetchone()
        is None
    )


def _count_answered(db, user_id, level_id):
    """Count questions the user has mastered (best score > 0)."""
    return db.execute(
        """SELECT COUNT(*) AS c
           FROM questions q
           WHERE q.level_id = ?
             AND COALESCE(
                 (SELECT MAX(points_earned)
                  FROM user_answers
                  WHERE user_id = ? AND question_id = q.id),
                 0
             ) > 0""",
        (level_id, user_id),
    ).fetchone()["c"]


def _has_more_questions(db, user_id, level_id):
    """Return True if there are still questions the user has not mastered."""
    return (
        db.execute(
            """SELECT 1 FROM questions q
               WHERE q.level_id = ?
                 AND COALESCE(
                     (SELECT MAX(points_earned)
                      FROM user_answers
                      WHERE user_id = ? AND question_id = q.id),
                     0
                 ) = 0
               LIMIT 1""",
            (level_id, user_id),
        ).fetchone()
        is not None
    )


def _question_difficulty(db, question_id):
    """Difficulty = 100 - average points earned. Higher = harder.

    Uses historical answer points when available; otherwise falls back to
    the average of the choice point values.
    """
    avg_earned = db.execute(
        "SELECT AVG(points_earned) FROM user_answers WHERE question_id = ?",
        (question_id,),
    ).fetchone()[0]
    if avg_earned is None:
        avg_earned = db.execute(
            "SELECT AVG(points) FROM choices WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
    return 100 - (avg_earned or 0)


def _pick_next_question(db, user_id, level_id):
    """Pick the next question to show.

    - First, show questions the user has not yet mastered (best == 0).
    - After all questions are mastered (>=25) but some are not perfect,
      retake only the non-100 questions (25/50 points) so the user can improve.
    - Once every question is 100 points, no retake is allowed.
    """
    last_points = _last_answer_points(db, user_id, level_id)

    questions = db.execute(
        "SELECT * FROM questions WHERE level_id = ?", (level_id,)
    ).fetchall()

    unmastered = []
    retakes = []
    for q in questions:
        best = _question_best_points(db, user_id, q["id"])
        if best == 0:
            difficulty = _question_difficulty(db, q["id"])
            unmastered.append((q, difficulty))
        elif best < 100:
            retakes.append(q)

    # If there are still unmastered questions, focus on those first.
    if unmastered:
        if last_points == 0:
            # Weighted random: easier questions (lower difficulty) are more likely.
            weights = [max(1, 100 - difficulty) for _, difficulty in unmastered]
        else:
            # Random among all unmastered questions.
            weights = None
        return random.choices([q for q, _ in unmastered], weights=weights, k=1)[0]

    # Level is completed; only retake questions worth less than 100 points.
    if retakes:
        return random.choice(retakes)

    return None


def _has_voucher(db, user_id, level_id):
    return (
        db.execute(
            "SELECT 1 FROM vouchers WHERE user_id = ? AND level_id = ?",
            (user_id, level_id),
        ).fetchone()
        is not None
    )


def _voucher_active(voucher_row):
    """Return True if this voucher has been used and is not yet expired."""
    if voucher_row is None or voucher_row["used_at"] is None or voucher_row["expires_at"] is None:
        return False
    return datetime.utcnow() < datetime.strptime(voucher_row["expires_at"], "%Y-%m-%d %H:%M:%S")


def _voucher_usable_for_new(voucher_row):
    """Return True if the existing voucher can be kept (not used or not expired)."""
    if voucher_row is None:
        return False
    if voucher_row["used_at"] is None:
        # Not yet used; keep the existing code.
        return True
    if voucher_row["expires_at"] is None:
        return False
    return datetime.utcnow() >= datetime.strptime(voucher_row["expires_at"], "%Y-%m-%d %H:%M:%S")


def _has_active_voucher(db, user_id):
    """Return True if the user currently has a live internet connection."""
    vouchers = db.execute("SELECT * FROM vouchers WHERE user_id = ?", (user_id,)).fetchall()
    return any(_voucher_active(v) for v in vouchers)


def _generate_voucher_code(db):
    """Generate a unique 8-character voucher code."""
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        if db.execute("SELECT 1 FROM vouchers WHERE code = ?", (code,)).fetchone() is None:
            return code


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
        answered = _count_answered(db, user_id, lvl["id"])

        level_summaries.append({
            "level": lvl,
            "status": progress["status"] if progress else "not_started",
            "total_points": progress["total_points"] if progress else 0,
            "max_points": total_questions * QUESTIONS_PER_LEVEL_MAX_POINTS,
            "answered": answered,
            "total_questions": total_questions,
            "is_perfect": _is_perfect(db, user_id, lvl["id"]),
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

    if current_app.config.get("BLOCK_TESTS_WHEN_ACTIVE") and _has_active_voucher(db, user_id):
        flash("You have an active internet connection. Wait for it to expire before taking another test.", "error")
        return redirect(url_for("main.internet_access"))

    _get_or_create_progress(db, user_id, lvl["id"])

    next_question = _pick_next_question(db, user_id, lvl["id"])

    if next_question is None:
        # All questions are mastered at 100 points, or the level is empty.
        return redirect(url_for("levels.results", level_number=level_number))

    choices = db.execute(
        "SELECT * FROM choices WHERE question_id = ? ORDER BY letter ASC",
        (next_question["id"],),
    ).fetchall()

    return render_template(
        "question.html",
        level=lvl,
        question=next_question,
        choices=choices,
    )


@bp.route("/<int:level_number>/answer", methods=("POST",))
@login_required
def answer(level_number):
    db = get_db()
    user_id = g.user["id"]

    if current_app.config.get("BLOCK_TESTS_WHEN_ACTIVE") and _has_active_voucher(db, user_id):
        flash("You have an active internet connection. Wait for it to expire before taking another test.", "error")
        return redirect(url_for("main.internet_access"))

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

    # Always record the attempt so the feedback/voucher pages can show the
    # latest result. The score only counts toward the total if it is better
    # than the user's previous best.
    prev_best = _question_best_points(db, user_id, question_id)

    db.execute(
        """INSERT INTO user_answers (user_id, question_id, choice_id, points_earned)
           VALUES (?, ?, ?, ?)""",
        (user_id, question_id, choice_id, choice["points"]),
    )

    point_delta = max(0, choice["points"] - prev_best)
    if point_delta > 0:
        db.execute(
            """UPDATE user_level_progress SET total_points = total_points + ?
               WHERE user_id = ? AND level_id = ?""",
            (point_delta, user_id, lvl["id"]),
        )
        db.execute(
            "UPDATE users SET points = points + ? WHERE id = ?",
            (point_delta, user_id),
        )
    db.commit()

    return redirect(url_for("levels.feedback", level_number=level_number, question_id=question_id))


@bp.route("/<int:level_number>/feedback/<int:question_id>")
@login_required
def feedback(level_number, question_id):
    db = get_db()
    user_id = g.user["id"]

    lvl = db.execute("SELECT * FROM levels WHERE level_number = ?", (level_number,)).fetchone()
    if lvl is None:
        flash("That level doesn't exist.", "error")
        return redirect(url_for("levels.index"))

    question = db.execute(
        "SELECT * FROM questions WHERE id = ? AND level_id = ?", (question_id, lvl["id"])
    ).fetchone()
    if question is None:
        flash("That question is not part of this level.", "error")
        return redirect(url_for("levels.play", level_number=level_number))

    answer_row = db.execute(
        """SELECT ua.*, c.letter, c.choice_text, c.points
           FROM user_answers ua
           JOIN choices c ON c.id = ua.choice_id
           WHERE ua.user_id = ? AND ua.question_id = ?
           ORDER BY ua.answered_at DESC, ua.id DESC
           LIMIT 1""",
        (user_id, question_id),
    ).fetchone()

    if answer_row is None:
        flash("Answer this question before viewing feedback.", "error")
        return redirect(url_for("levels.play", level_number=level_number))

    correct_choice = db.execute(
        """SELECT * FROM choices
           WHERE question_id = ? AND points = 100
           ORDER BY letter ASC LIMIT 1""",
        (question_id,),
    ).fetchone()

    total_questions = db.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
    ).fetchone()["c"]
    is_completed = _count_answered(db, user_id, lvl["id"]) == total_questions
    is_perfect = _is_perfect(db, user_id, lvl["id"])
    has_more = not is_perfect

    has_voucher = _has_voucher(db, user_id, lvl["id"])

    return render_template(
        "feedback.html",
        level=lvl,
        question=question,
        answer=answer_row,
        correct_choice=correct_choice,
        has_more=has_more,
        is_completed=is_completed,
        is_perfect=is_perfect,
        has_voucher=has_voucher,
    )


@bp.route("/<int:level_number>/voucher")
@login_required
def voucher(level_number):
    db = get_db()
    user_id = g.user["id"]

    lvl = db.execute("SELECT * FROM levels WHERE level_number = ?", (level_number,)).fetchone()
    if lvl is None:
        flash("That level doesn't exist.", "error")
        return redirect(url_for("levels.index"))

    # A voucher requires at least one non-zero (>=25) answer in this level.
    has_earned = db.execute(
        """SELECT 1 FROM user_answers ua
           JOIN questions q ON q.id = ua.question_id
           WHERE ua.user_id = ? AND q.level_id = ? AND ua.points_earned >= 25
           LIMIT 1""",
        (user_id, lvl["id"]),
    ).fetchone()

    if not has_earned:
        flash("Answer at least one question correctly to earn an access voucher.", "error")
        return redirect(url_for("levels.play", level_number=level_number))

    # Use the question from the query string if provided, otherwise the latest
    # non-zero answer in this level (the one that earned the voucher).
    question_id = request.args.get("question_id", type=int)
    if question_id:
        answer_row = db.execute(
            """SELECT ua.*, c.letter, c.choice_text, c.points
               FROM user_answers ua
               JOIN choices c ON c.id = ua.choice_id
               WHERE ua.user_id = ? AND ua.question_id = ?
               ORDER BY ua.answered_at DESC, ua.id DESC
               LIMIT 1""",
            (user_id, question_id),
        ).fetchone()
    else:
        answer_row = db.execute(
            """SELECT ua.*, c.letter, c.choice_text, c.points, q.id AS question_id
               FROM user_answers ua
               JOIN choices c ON c.id = ua.choice_id
               JOIN questions q ON q.id = ua.question_id
               WHERE ua.user_id = ? AND q.level_id = ? AND ua.points_earned >= 25
               ORDER BY ua.answered_at DESC, ua.id DESC
               LIMIT 1""",
            (user_id, lvl["id"]),
        ).fetchone()

    # If the user retook a mastered question and got 25-99 on this attempt,
    # the level-wide >=25 query may not match (best could still be 100), but
    # we still want to display this attempt on the voucher page.
    if answer_row is None and question_id:
        answer_row = db.execute(
            """SELECT ua.*, c.letter, c.choice_text, c.points
               FROM user_answers ua
               JOIN choices c ON c.id = ua.choice_id
               WHERE ua.user_id = ? AND ua.question_id = ?
               ORDER BY ua.answered_at DESC, ua.id DESC
               LIMIT 1""",
            (user_id, question_id),
        ).fetchone()

    if answer_row is None:
        flash("No qualifying answer found.", "error")
        return redirect(url_for("levels.play", level_number=level_number))

    question_id = question_id or answer_row["question_id"]
    question = db.execute(
        "SELECT * FROM questions WHERE id = ? AND level_id = ?", (question_id, lvl["id"])
    ).fetchone()
    if question is None:
        flash("That question is not part of this level.", "error")
        return redirect(url_for("levels.play", level_number=level_number))

    correct_choice = db.execute(
        """SELECT * FROM choices
           WHERE question_id = ? AND points = 100
           ORDER BY letter ASC LIMIT 1""",
        (question_id,),
    ).fetchone()

    voucher_row = db.execute(
        "SELECT * FROM vouchers WHERE user_id = ? AND level_id = ?",
        (user_id, lvl["id"]),
    ).fetchone()

    if not _voucher_usable_for_new(voucher_row):
        code = _generate_voucher_code(db)
        if voucher_row is None:
            db.execute(
                """INSERT INTO vouchers (user_id, level_id, code)
                   VALUES (?, ?, ?)""",
                (user_id, lvl["id"], code),
            )
        else:
            db.execute(
                """UPDATE vouchers
                   SET code = ?, created_at = CURRENT_TIMESTAMP, used_at = NULL, expires_at = NULL
                   WHERE id = ?""",
                (code, voucher_row["id"]),
            )
        db.commit()
        voucher_row = db.execute(
            "SELECT * FROM vouchers WHERE user_id = ? AND level_id = ?",
            (user_id, lvl["id"]),
        ).fetchone()

    total_questions = db.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
    ).fetchone()["c"]
    is_completed = _count_answered(db, user_id, lvl["id"]) == total_questions
    is_perfect = _is_perfect(db, user_id, lvl["id"])
    has_more = not is_perfect

    return render_template(
        "voucher.html",
        level=lvl,
        question=question,
        answer=answer_row,
        correct_choice=correct_choice,
        voucher=voucher_row,
        has_more=has_more,
        is_completed=is_completed,
        is_perfect=is_perfect,
    )


@bp.route("/<int:level_number>/connect", methods=("GET", "POST"))
@login_required
def connect(level_number):
    db = get_db()
    user_id = g.user["id"]

    lvl = db.execute("SELECT * FROM levels WHERE level_number = ?", (level_number,)).fetchone()
    if lvl is None:
        flash("That level doesn't exist.", "error")
        return redirect(url_for("levels.index"))

    voucher_row = db.execute(
        "SELECT * FROM vouchers WHERE user_id = ? AND level_id = ?",
        (user_id, lvl["id"]),
    ).fetchone()

    if voucher_row is None:
        flash("Earn a voucher for this level first.", "error")
        return redirect(url_for("levels.play", level_number=level_number))

    already_used = False
    if request.method == "POST":
        pasted = request.form.get("voucher_code", "").strip().upper()
        if pasted == voucher_row["code"].upper():
            if voucher_row["used_at"] is not None:
                already_used = True
                if _voucher_active(voucher_row):
                    flash("This voucher is already connected and active.", "info")
                else:
                    flash("This voucher has expired. Take the test again for a new one.", "error")
            else:
                db.execute(
                    """UPDATE vouchers
                       SET used_at = CURRENT_TIMESTAMP,
                           expires_at = datetime('now', '+5 hours')
                       WHERE id = ?""",
                    (voucher_row["id"],),
                )
                db.commit()
                voucher_row = db.execute(
                    "SELECT * FROM vouchers WHERE user_id = ? AND level_id = ?",
                    (user_id, lvl["id"]),
                ).fetchone()
            return render_template(
                "connect.html", level=lvl, voucher=voucher_row,
                connected=_voucher_active(voucher_row), is_expired=False,
                already_used=already_used,
            )
        flash("That code does not match your voucher.", "error")

    is_expired = voucher_row["used_at"] is not None and not _voucher_active(voucher_row)
    return render_template(
        "connect.html",
        level=lvl,
        voucher=voucher_row,
        connected=_voucher_active(voucher_row),
        is_expired=is_expired,
        already_used=False,
    )


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
    answered = _count_answered(db, user_id, lvl["id"])

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

    # Per-question breakdown for review: show the best attempt per question.
    rows = db.execute(
        """SELECT q.question_number, q.prompt,
                  c.letter AS chosen_letter, c.choice_text AS chosen_text, c.points,
                  best.letter AS best_letter, best.choice_text AS best_text
           FROM questions q
           LEFT JOIN user_answers ua ON ua.id = (
               SELECT id FROM user_answers
               WHERE user_id = ? AND question_id = q.id
               ORDER BY points_earned DESC, id DESC
               LIMIT 1
           )
           LEFT JOIN choices c ON c.id = ua.choice_id
           LEFT JOIN choices best ON best.question_id = q.id AND best.points = 100
           WHERE q.level_id = ?
           ORDER BY q.question_number ASC""",
        (user_id, lvl["id"]),
    ).fetchall()

    next_level = db.execute(
        "SELECT * FROM levels WHERE level_number = ?", (level_number + 1,)
    ).fetchone()

    has_voucher = _has_voucher(db, user_id, lvl["id"])

    return render_template(
        "results.html",
        level=lvl,
        total_points=progress["total_points"],
        max_points=max_points,
        percentage=percentage,
        rows=rows,
        next_level=next_level,
        has_voucher=has_voucher,
    )
