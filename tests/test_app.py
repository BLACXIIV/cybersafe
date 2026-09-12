from io import BytesIO

import openpyxl
import pytest
from app import create_app
from security import MIN_LENGTH, validate_password, describe_problems


@pytest.fixture
def app():
    app = create_app()
    app.config.update({"TESTING": True, "RATELIMIT_ENABLED": False})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_landing_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Learn Cybersecurity" in response.data
    assert b"Earn Your Internet Access" in response.data
    assert b"circuit-bg" in response.data


# ---------- Password policy ----------

STRONG_PASSWORDS = [
    "Tromb0ne#Ledge",
    "Velvet$Mango47",
    "9Kettle!Prism",
    "brisk-Walnut8Q",
]


@pytest.mark.parametrize("password", STRONG_PASSWORDS)
def test_strong_passwords_are_accepted(password):
    assert validate_password(password) == []


@pytest.mark.parametrize("password, reason", [
    ("Ab1!def", "shorter than the minimum"),
    ("verylongpassword1!", "no uppercase letter"),
    ("VERYLONGPASSWORD1!", "no lowercase letter"),
    ("VeryLongPassword!", "no number"),
    ("VeryLongPassword1", "no symbol"),
    ("Password123!", "common word with a trailing counter"),
    ("P@ssw0rd!2024", "leetspeak spelling of a common word"),
    ("Qwerty!12345", "keyboard walk"),
    ("Abcdefgh1!xy", "alphabet run"),
    ("Zaaa!ntholog9", "same character three times"),
    ("Aa1!Aa1!Aa1!", "a short block repeated"),
    ("Ab1!Ab1!Ab1", "too few distinct characters, though not an exact repeat"),
    ("MyCyberSafe1!", "contains the app name"),
])
def test_weak_passwords_are_rejected(password, reason):
    assert validate_password(password), f"should have been rejected: {reason}"


@pytest.mark.parametrize("password", [
    "JuanDelaCruz1!",
    "jdelacruz#2024X",
    "Nothing!butJuan9",
])
def test_passwords_built_from_personal_details_are_rejected(password):
    problems = validate_password(
        password,
        personal_values=("Juan Dela Cruz", "jdelacruz", "juan.delacruz@school.edu"),
    )
    assert "not contain your name, username, or email" in problems


def test_email_domain_does_not_block_unrelated_passwords():
    """'com' and 'school' come from the domain and must not be treated as personal."""
    assert validate_password(
        "Velvet$Mango47", personal_values=("Ana Reyes", "areyes", "areyes@school.com")
    ) == []


def test_describe_problems_builds_one_sentence():
    assert describe_problems([]) is None
    assert describe_problems(["include a number"]) == "Your password must include a number."
    combined = describe_problems(["include a number", "include a symbol"])
    assert combined == "Your password must include a number; and include a symbol."


def _pre_register_student(client, username="weakpwuser"):
    app = client.application
    with app.app_context():
        from database.db import get_db
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES (?, ?, ?, '', 'Grade 10', 'student', 0)""",
            ("Test Student", username, f"{username}@cybersafe.local"),
        )
        db.execute(
            "UPDATE users SET is_password_set = 0, password_hash = '' WHERE username = ?",
            (username,),
        )
        db.commit()


def test_signup_rejects_weak_password(client):
    _pre_register_student(client)
    client.post("/login", data={"identifier": "weakpwuser", "step": "1"})
    response = client.post("/login", data={
        "identifier": "weakpwuser",
        "step": "2",
        "password": "password123",
        "confirm_password": "password123",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Your password must" in response.data


def test_signup_page_shows_the_rules(client):
    _pre_register_student(client, username="rulespwuser")
    response = client.post("/login", data={"identifier": "rulespwuser", "step": "1"})
    assert response.status_code == 200
    assert b"pw-rules" in response.data
    assert f"At least {MIN_LENGTH} characters".encode() in response.data


# ---------- Question bulk import ----------

QUESTION_IMPORT_HEADERS = [
    "Mission Number", "Question Number", "Question",
    "Choice A", "Choice A Points", "Choice B", "Choice B Points",
    "Choice C", "Choice C Points", "Choice D", "Choice D Points",
    "Explanation",
]


def _make_questions_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(QUESTION_IMPORT_HEADERS)
    for row in rows:
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


class _Upload:
    """Minimal stand-in for a Werkzeug FileStorage (only .read() is used)."""

    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def test_import_questions_from_excel(client):
    from admin import _import_questions_from_excel
    from database.db import get_db

    app = client.application
    with app.app_context():
        db = get_db()

        def _wipe_test_data():
            db.execute(
                """DELETE FROM choices WHERE question_id IN
                   (SELECT id FROM questions WHERE prompt LIKE 'IMPORT-TEST%')"""
            )
            db.execute("DELETE FROM questions WHERE prompt LIKE 'IMPORT-TEST%'")
            db.execute("DELETE FROM levels WHERE level_number IN (998, 999)")

        _wipe_test_data()
        db.execute(
            "INSERT INTO levels (level_number, name, focus) VALUES (999, 'Import Test Mission', 'test')"
        )
        db.commit()
        level_id = db.execute(
            "SELECT id FROM levels WHERE level_number = 999"
        ).fetchone()["id"]

        rows = [
            # valid row -> added
            [999, 1, "IMPORT-TEST valid question", "Best", 100, "Better", 50, "Weak", 25, "Wrong", 0, "Because."],
            # missing Choice D text -> error, not imported
            [999, 2, "IMPORT-TEST missing choice", "Best", 100, "Better", 50, "Weak", 25, None, None, ""],
            # mission that does not exist -> auto-created, question imported
            [998, 1, "IMPORT-TEST new mission", "Best", 100, "Better", 50, "Weak", 25, "Wrong", 0, ""],
            # same level + question_number as the valid row -> skipped
            [999, 1, "IMPORT-TEST duplicate", "Best", 100, "Better", 50, "Weak", 25, "Wrong", 0, ""],
        ]

        try:
            added, skipped, errors, created = _import_questions_from_excel(
                db, _Upload(_make_questions_xlsx(rows))
            )

            assert added == 2
            assert skipped == 1
            assert created == [998]
            assert any("missing" in e.lower() for e in errors)

            # The missing mission was auto-created with a placeholder name.
            new_level = db.execute(
                "SELECT * FROM levels WHERE level_number = 998"
            ).fetchone()
            assert new_level is not None
            assert new_level["name"] == "Mission 998"
            new_question = db.execute(
                "SELECT * FROM questions WHERE level_id = ? AND question_number = 1",
                (new_level["id"],),
            ).fetchone()
            assert new_question is not None
            assert new_question["prompt"] == "IMPORT-TEST new mission"

            question = db.execute(
                "SELECT * FROM questions WHERE level_id = ? AND question_number = 1",
                (level_id,),
            ).fetchone()
            assert question is not None
            assert question["prompt"] == "IMPORT-TEST valid question"
            assert question["explanation"] == "Because."

            choices = db.execute(
                "SELECT letter, points FROM choices WHERE question_id = ? ORDER BY letter",
                (question["id"],),
            ).fetchall()
            assert [(c["letter"], c["points"]) for c in choices] == [
                ("A", 100), ("B", 50), ("C", 25), ("D", 0),
            ]

            # The missing-choice row must not have been imported.
            assert db.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE level_id = ?", (level_id,)
            ).fetchone()["c"] == 1
        finally:
            _wipe_test_data()
            db.commit()
