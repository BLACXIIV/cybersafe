"""
Seed the levels/questions/choices tables from database/questions_data.json.

Run this after `flask --app app init-db` (or after the app auto-creates the
DB on first run) to load the Cyber-S.A.F.E. question bank:

    python database/seed_questions.py

Safe to re-run: it clears and reloads levels/questions/choices each time,
so it won't create duplicates. It does NOT touch the users table.
"""
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(BASE_DIR, "database", "cybersafe.db")
JSON_PATH = os.path.join(BASE_DIR, "database", "questions_data.json")


def seed():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}.")
        print("Run the app once (python app.py) or `flask --app app init-db` first.")
        sys.exit(1)

    with open(JSON_PATH, "r") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # Clear existing question data so this script is safely re-runnable.
    cur.execute("DELETE FROM choices")
    cur.execute("DELETE FROM questions")
    cur.execute("DELETE FROM levels")

    level_count = 0
    question_count = 0
    choice_count = 0

    for level in data["levels"]:
        cur.execute(
            "INSERT INTO levels (level_number, name, focus) VALUES (?, ?, ?)",
            (level["level_number"], level["name"], level.get("focus")),
        )
        level_id = cur.lastrowid
        level_count += 1

        for q in level["questions"]:
            cur.execute(
                "INSERT INTO questions (level_id, question_number, prompt) VALUES (?, ?, ?)",
                (level_id, q["question_number"], q["prompt"]),
            )
            question_id = cur.lastrowid
            question_count += 1

            for choice in q["choices"]:
                cur.execute(
                    """INSERT INTO choices (question_id, letter, choice_text, points)
                       VALUES (?, ?, ?, ?)""",
                    (question_id, choice["letter"], choice["text"], choice["points"]),
                )
                choice_count += 1

    conn.commit()
    conn.close()

    print(f"Seeded {level_count} levels, {question_count} questions, {choice_count} choices.")


if __name__ == "__main__":
    seed()
