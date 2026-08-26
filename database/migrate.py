"""
Apply schema and data migrations to an existing cyberafe.db without
losing user accounts or progress.

Run after updating schema.sql or questions_data.json:

    python database/migrate.py

What it does:
- Adds `questions.explanation` if missing.
- Creates the `vouchers` table if missing.
- Updates questions/choices from `database/questions_data.json`.
- Recomputes stored points to match the latest choice point values.
"""
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(BASE_DIR, "database", "cybersafe.db")
JSON_PATH = os.path.join(BASE_DIR, "database", "questions_data.json")


def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}.")
        sys.exit(1)

    with open(JSON_PATH, "r") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # 1. Add questions.explanation if it does not exist.
    columns = {row[1] for row in cur.execute("PRAGMA table_info(questions)").fetchall()}
    if "explanation" not in columns:
        cur.execute("ALTER TABLE questions ADD COLUMN explanation TEXT")
        print("Added questions.explanation column.")

    # 2. Create vouchers table if it does not exist.
    tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "vouchers" not in tables:
        cur.execute(
            """CREATE TABLE vouchers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                level_id   INTEGER NOT NULL REFERENCES levels(id),
                code       TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                used_at    TIMESTAMP,
                UNIQUE(user_id, level_id)
            )"""
        )
        print("Created vouchers table.")

    # 3. Update questions and choices from JSON.
    level_count = 0
    question_count = 0
    choice_count = 0

    for level in data["levels"]:
        level_row = cur.execute(
            "SELECT id FROM levels WHERE level_number = ?", (level["level_number"],)
        ).fetchone()
        if level_row is None:
            cur.execute(
                "INSERT INTO levels (level_number, name, focus) VALUES (?, ?, ?)",
                (level["level_number"], level["name"], level.get("focus")),
            )
            level_id = cur.lastrowid
            print(f"Inserted missing level {level['level_number']}.")
        else:
            level_id = level_row[0]
            cur.execute(
                "UPDATE levels SET name = ?, focus = ? WHERE id = ?",
                (level["name"], level.get("focus"), level_id),
            )
        level_count += 1

        for q in level["questions"]:
            question_row = cur.execute(
                "SELECT id FROM questions WHERE level_id = ? AND question_number = ?",
                (level_id, q["question_number"]),
            ).fetchone()
            explanation = q.get("explanation")
            if question_row is None:
                cur.execute(
                    """INSERT INTO questions (level_id, question_number, prompt, explanation)
                       VALUES (?, ?, ?, ?)""",
                    (level_id, q["question_number"], q["prompt"], explanation),
                )
                question_id = cur.lastrowid
                print(f"Inserted missing L{level['level_number']} Q{q['question_number']}.")
            else:
                question_id = question_row[0]
                cur.execute(
                    "UPDATE questions SET prompt = ?, explanation = ? WHERE id = ?",
                    (q["prompt"], explanation, question_id),
                )
            question_count += 1

            for choice in q["choices"]:
                choice_row = cur.execute(
                    "SELECT id FROM choices WHERE question_id = ? AND letter = ?",
                    (question_id, choice["letter"]),
                ).fetchone()
                if choice_row is None:
                    cur.execute(
                        """INSERT INTO choices (question_id, letter, choice_text, points)
                           VALUES (?, ?, ?, ?)""",
                        (question_id, choice["letter"], choice["text"], choice["points"]),
                    )
                    print(f"Inserted missing choice {choice['letter']} for L{level['level_number']} Q{q['question_number']}.")
                else:
                    cur.execute(
                        """UPDATE choices
                           SET choice_text = ?, points = ?
                           WHERE id = ?""",
                        (choice["text"], choice["points"], choice_row[0]),
                    )
                choice_count += 1

    # 4. Recompute stored points to match the latest choice values.
    # Fix user_answers.points_earned to match the current choice point value.
    cur.execute(
        """UPDATE user_answers
           SET points_earned = (
               SELECT c.points FROM choices c WHERE c.id = user_answers.choice_id
           )
           WHERE choice_id IS NOT NULL"""
    )

    # Recompute user_level_progress.total_points.
    cur.execute(
        """UPDATE user_level_progress
           SET total_points = (
               SELECT COALESCE(SUM(ua.points_earned), 0)
               FROM user_answers ua
               JOIN questions q ON q.id = ua.question_id
               WHERE ua.user_id = user_level_progress.user_id
                 AND q.level_id = user_level_progress.level_id
           )"""
    )

    # Recompute users.points.
    cur.execute(
        """UPDATE users
           SET points = (
               SELECT COALESCE(SUM(ua.points_earned), 0)
               FROM user_answers ua
               WHERE ua.user_id = users.id
           )"""
    )

    conn.commit()
    conn.close()

    print(f"Updated {level_count} levels, {question_count} questions, {choice_count} choices.")
    print("Recomputed points.")


if __name__ == "__main__":
    migrate()
