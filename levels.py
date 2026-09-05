import random
import secrets
import string
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, g, flash, current_app, session

from auth import login_required, student_required
from database.db import get_db
from ranks import BADGE_ORDER, rank_info
import network_access

VOUCHER_DURATION_SECONDS = 5 * 60 * 60  # keep in sync with the "+5 hours" SQL below

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


def _pending_points(db, user_id):
    """Return the global user points that have been recorded in level progress
    but not yet added to users.points (i.e. waiting for a voucher connection)."""
    progress_total = db.execute(
        "SELECT COALESCE(SUM(total_points), 0) FROM user_level_progress WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    user_points = db.execute(
        "SELECT points FROM users WHERE id = ?", (user_id,)
    ).fetchone()["points"]
    return max(0, progress_total - user_points)


def _claim_pending_points(db, user_id):
    """Add all pending points to users.points, sync the level title, and mark
    the corresponding answers as claimed.

    A rank-up is detected here (not when the question is answered) so the
    celebration is shown once the points are actually credited to the user.
    """
    pending = _pending_points(db, user_id)

    db.execute("UPDATE user_answers SET claimed = 1 WHERE user_id = ? AND claimed = 0", (user_id,))

    if pending <= 0:
        return 0

    total_questions = db.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
    max_points = total_questions * QUESTIONS_PER_LEVEL_MAX_POINTS
    old_points = db.execute("SELECT points FROM users WHERE id = ?", (user_id,)).fetchone()["points"]
    old_badge, old_title, _, _, _, _ = rank_info(old_points, max_points)

    db.execute("UPDATE users SET points = points + ? WHERE id = ?", (pending, user_id))

    new_points = db.execute("SELECT points FROM users WHERE id = ?", (user_id,)).fetchone()["points"]
    new_badge, new_title, _, _, _, _ = rank_info(new_points, max_points)
    db.execute("UPDATE users SET level = ? WHERE id = ?", (new_title, user_id))

    if BADGE_ORDER.get(new_badge, 0) > BADGE_ORDER.get(old_badge, 0):
        session["rank_up"] = {
            "old_badge": old_badge,
            "old_title": old_title,
            "new_badge": new_badge,
            "new_title": new_title,
        }

    return pending


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


def _pick_next_question(db, user_id, level_id, exclude_question_id=None):
    """Pick the next question to show.

    - First, show questions the user has not yet mastered (best == 0).
    - After all questions are mastered (>=25) but some are not perfect,
      retake only the non-100 questions (25/50 points) so the user can improve.
    - Once every question is 100 points, no retake is allowed.
    - `exclude_question_id` skips the question the user just came from, as long
      as another candidate exists (used by the "Next Question" button).
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

    if exclude_question_id is not None:
        if len(unmastered) > 1:
            unmastered = [pair for pair in unmastered if pair[0]["id"] != exclude_question_id]
        if len(retakes) > 1:
            retakes = [q for q in retakes if q["id"] != exclude_question_id]

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
    """Return True if the existing voucher should be kept.

    We keep an existing voucher if it has never been used, or if it is
    currently still active (used and not yet expired). If it has expired,
    a new one must be generated.
    """
    if voucher_row is None:
        return False
    if voucher_row["used_at"] is None:
        # Not yet used; keep the existing code.
        return True
    return _voucher_active(voucher_row)


def _has_active_voucher(db, user_id):
    """Return True if the user currently has a live internet connection."""
    vouchers = db.execute("SELECT * FROM vouchers WHERE user_id = ?", (user_id,)).fetchall()
    return any(_voucher_active(v) for v in vouchers)


def _activate_voucher(db, voucher_row):
    """Mark a voucher as used/active AND actually open the gate for the
    device that redeemed it.

    Looks up the caller's MAC address from the Pi's ARP table (this only
    works when the request really came in over the AP subnet — e.g. not
    when testing from `localhost`) and grants it internet access for
    VOUCHER_DURATION_SECONDS. The DB row is always updated regardless of
    whether the network grant succeeds, so app behaviour on a dev machine
    without the Pi firewall installed is unchanged.
    """
    ip_address = request.remote_addr
    mac_address = network_access.get_mac_for_ip(ip_address)

    db.execute(
        """UPDATE vouchers
           SET used_at = CURRENT_TIMESTAMP,
               expires_at = datetime('now', '+5 hours'),
               ip_address = ?,
               mac_address = ?
           WHERE id = ?""",
        (ip_address, mac_address, voucher_row["id"]),
    )

    if mac_address:
        granted = network_access.grant_internet_access(mac_address, VOUCHER_DURATION_SECONDS)
        if not granted:
            current_app.logger.warning(
                "Voucher %s activated but firewall grant failed for MAC %s (is network/setup_ap.sh installed?)",
                voucher_row["code"], mac_address,
            )
    else:
        current_app.logger.info(
            "Voucher %s activated but no MAC address found for IP %s; "
            "internet access was not opened at the firewall.",
            voucher_row["code"], ip_address,
        )


def _deactivate_voucher(db, voucher_row):
    """Turn a voucher off early (student toggled internet access off)."""
    db.execute(
        "UPDATE vouchers SET used_at = NULL, expires_at = NULL WHERE id = ?",
        (voucher_row["id"],),
    )
    if voucher_row["mac_address"]:
        network_access.revoke_internet_access(voucher_row["mac_address"])


def _generate_voucher_code(db):
    """Generate a unique 8-character voucher code."""
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        if db.execute("SELECT 1 FROM vouchers WHERE code = ?", (code,)).fetchone() is None:
            return code


def _ensure_voucher(db, user_id, level_id):
    """Make sure the user has a usable voucher stored for this level.

    The voucher is persisted as soon as it is earned (not when the voucher page
    is opened), so it always shows up in the voucher history even if the user
    leaves the feedback page without clicking "Use Voucher".

    Returns (voucher_row, regenerated) where `regenerated` is True when an
    expired voucher was replaced with a fresh code.
    """
    voucher_row = db.execute(
        "SELECT * FROM vouchers WHERE user_id = ? AND level_id = ?",
        (user_id, level_id),
    ).fetchone()

    if _voucher_usable_for_new(voucher_row):
        return voucher_row, False

    code = _generate_voucher_code(db)
    regenerated = voucher_row is not None
    if voucher_row is None:
        db.execute(
            """INSERT INTO vouchers (user_id, level_id, code)
               VALUES (?, ?, ?)""",
            (user_id, level_id, code),
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
        (user_id, level_id),
    ).fetchone()
    return voucher_row, regenerated


@bp.route("/")
@login_required
def index():
    db = get_db()
    user_id = g.user["id"]
    all_levels = db.execute("SELECT * FROM levels ORDER BY level_number").fetchall()
    is_admin = g.user["role"] == "admin"

    level_summaries = []
    for lvl in all_levels:
        total_questions = db.execute(
            "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (lvl["id"],)
        ).fetchone()["c"]

        if is_admin:
            # Admins get a read-only view of all missions; no progress needed.
            level_summaries.append({
                "level": lvl,
                "status": "not_started",
                "total_points": 0,
                "max_points": total_questions * QUESTIONS_PER_LEVEL_MAX_POINTS,
                "answered": 0,
                "total_questions": total_questions,
                "is_perfect": False,
                "unlocked": True,
            })
            continue

        progress = db.execute(
            "SELECT * FROM user_level_progress WHERE user_id = ? AND level_id = ?",
            (user_id, lvl["id"]),
        ).fetchone()
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


@bp.route("/<int:level_number>/view")
@login_required
def view(level_number):
    """Read-only preview of a level's questions, choices, correct answers, and
    explanations. Only admins can access it; students are sent to the play page."""
    if g.user["role"] != "admin":
        return redirect(url_for("levels.play", level_number=level_number))

    db = get_db()
    lvl = db.execute("SELECT * FROM levels WHERE level_number = ?", (level_number,)).fetchone()
    if lvl is None:
        flash("That level doesn't exist.", "error")
        return redirect(url_for("levels.index"))

    questions = db.execute(
        "SELECT * FROM questions WHERE level_id = ? ORDER BY question_number",
        (lvl["id"],),
    ).fetchall()

    items = []
    for q in questions:
        choices = db.execute(
            "SELECT * FROM choices WHERE question_id = ? ORDER BY letter",
            (q["id"],),
        ).fetchall()
        correct = next((c for c in choices if c["points"] == 100), None)
        items.append({"question": q, "choices": choices, "correct": correct})

    return render_template("level_view.html", level=lvl, items=items)


@bp.route("/<int:level_number>/play")
@student_required
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

    if _pending_points(db, user_id) > 0:
        flash("You have unclaimed points. Use a voucher to connect and claim them before taking another test.", "error")
        return redirect(url_for("main.internet_access"))

    _get_or_create_progress(db, user_id, lvl["id"])

    next_question = _pick_next_question(
        db, user_id, lvl["id"], exclude_question_id=request.args.get("skip", type=int)
    )

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
@student_required
def answer(level_number):
    db = get_db()
    user_id = g.user["id"]

    if current_app.config.get("BLOCK_TESTS_WHEN_ACTIVE") and _has_active_voucher(db, user_id):
        flash("You have an active internet connection. Wait for it to expire before taking another test.", "error")
        return redirect(url_for("main.internet_access"))

    if _pending_points(db, user_id) > 0:
        flash("You have unclaimed points. Use a voucher to connect and claim them before taking another test.", "error")
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
        """INSERT INTO user_answers (user_id, question_id, choice_id, points_earned, claimed)
           VALUES (?, ?, ?, ?, 0)""",
        (user_id, question_id, choice_id, choice["points"]),
    )

    point_delta = max(0, choice["points"] - prev_best)
    if point_delta > 0:
        # Update level progress immediately, but keep global user points as
        # "pending" until the voucher is actually used to connect.
        db.execute(
            """UPDATE user_level_progress SET total_points = total_points + ?
               WHERE user_id = ? AND level_id = ?""",
            (point_delta, user_id, lvl["id"]),
        )
        # The rank-up celebration is not triggered here: the points are only
        # pending until a voucher is used, so it fires in _claim_pending_points.

    # Persist the voucher right away when the answer actually leaves points
    # pending, so it is listed in the voucher history even if the user never
    # opens the voucher page.
    if point_delta > 0:
        _ensure_voucher(db, user_id, lvl["id"])

    db.commit()

    return redirect(url_for("levels.feedback", level_number=level_number, question_id=question_id))


@bp.route("/<int:level_number>/feedback/<int:question_id>")
@student_required
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

    # Points are pending until a voucher is used to connect. Show how many
    # global points are on the line for this answer.
    pending_points = db.execute(
        "SELECT COALESCE(SUM(total_points), 0) FROM user_level_progress WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0] - g.user["points"]
    if pending_points < 0:
        pending_points = 0

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
        pending_points=pending_points,
    )


@bp.route("/<int:level_number>/voucher")
@student_required
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

    voucher_row, regenerated = _ensure_voucher(db, user_id, lvl["id"])
    if regenerated:
        flash("Your old voucher expired. A new voucher code has been generated.", "info")

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
@student_required
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

    if request.method == "POST":
        # Always redirect after a POST so a refresh or Back does not ask the
        # browser to resubmit the form (Confirm Form Resubmission).
        already_used = False
        pasted = request.form.get("voucher_code", "").strip().upper()
        if pasted != voucher_row["code"].upper():
            flash("That code does not match your voucher.", "error")
        elif voucher_row["used_at"] is not None:
            already_used = True
            if _voucher_active(voucher_row):
                flash("This voucher is already connected and active.", "info")
            else:
                flash("This voucher has expired. Take the test again for a new one.", "error")
        else:
            _activate_voucher(db, voucher_row)
            # Connecting to the internet is when pending points become real.
            claimed = _claim_pending_points(db, user_id)
            db.commit()
            if claimed > 0:
                flash(f"Connected! You claimed {claimed} points.", "success")
            else:
                flash("Connected to the internet.", "success")

        return redirect(
            url_for(
                "levels.connect",
                level_number=level_number,
                already=1 if already_used else None,
            )
        )

    is_expired = voucher_row["used_at"] is not None and not _voucher_active(voucher_row)
    return render_template(
        "connect.html",
        level=lvl,
        voucher=voucher_row,
        connected=_voucher_active(voucher_row),
        is_expired=is_expired,
        already_used=request.args.get("already") == "1",
    )


@bp.route("/<int:level_number>/results")
@student_required
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
