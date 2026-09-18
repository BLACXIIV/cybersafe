from io import BytesIO

import openpyxl
import pytest
from app import create_app
from security import MIN_LENGTH, validate_password, describe_problems


@pytest.fixture
def app():
    app = create_app()
    app.config.update({
        "TESTING": True,
        "RATELIMIT_ENABLED": False,
        # The test client's default Host is "localhost" — make that the
        # configured real host so only genuinely foreign Host headers
        # (captive-portal probes) trigger the before_request redirect.
        "PORTAL_BASE_URL": "http://localhost",
    })
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


# ---------- Voucher device lock ----------

def test_login_blocked_while_voucher_active_on_another_ip(client):
    """An account with an active voucher on one IP cannot log in from a
    different IP; the same IP is allowed to reconnect."""
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    voucher_ip = "10.0.0.55"
    other_ip = "10.0.0.99"

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM vouchers WHERE code = 'TESTLOCK'")
        db.execute("DELETE FROM users WHERE username = 'voucherlock'")
        db.execute("DELETE FROM levels WHERE level_number = 997")
        level = db.execute(
            "SELECT id FROM levels ORDER BY level_number LIMIT 1"
        ).fetchone()
        if level is None:
            db.execute("INSERT INTO levels (level_number, name) VALUES (997, 'Voucher Lock Test')")
            level = db.execute("SELECT id FROM levels WHERE level_number = 997").fetchone()
        db.execute(
            """INSERT INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
            ("Voucher Lock", "voucherlock", "voucherlock@cybersafe.local",
             generate_password_hash("V0ucher#Lock")),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username = 'voucherlock'"
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO vouchers (user_id, level_id, code, used_at, expires_at, ip_address)
               VALUES (?, ?, 'TESTLOCK', CURRENT_TIMESTAMP, datetime('now', '+1 hours'), ?)""",
            (user_id, level["id"], voucher_ip),
        )
        db.commit()

    try:
        # Full login from a different IP -> blocked at step 2 before the
        # session is created.
        client.post(
            "/login",
            data={"identifier": "voucherlock", "step": "1"},
            environ_overrides={"REMOTE_ADDR": other_ip},
        )
        response = client.post(
            "/login",
            data={"identifier": "voucherlock", "step": "2", "password": "V0ucher#Lock"},
            headers={"X-Requested-With": "XMLHttpRequest"},
            environ_overrides={"REMOTE_ADDR": other_ip},
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["ok"] is False
        assert "active internet connection on another device" in body["error"]
        assert "one device at a time" in body["hint"]

        # Same account logging in from the voucher's own IP -> allowed.
        client.post(
            "/login",
            data={"identifier": "voucherlock", "step": "1"},
            environ_overrides={"REMOTE_ADDR": voucher_ip},
        )
        response = client.post(
            "/login",
            data={"identifier": "voucherlock", "step": "2", "password": "V0ucher#Lock"},
            environ_overrides={"REMOTE_ADDR": voucher_ip},
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers["Location"]
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM vouchers WHERE code = 'TESTLOCK'")
            db.execute("DELETE FROM users WHERE username = 'voucherlock'")
            db.execute("DELETE FROM levels WHERE level_number = 997")
            db.commit()


# ---------- Captive-portal connect success page ----------

def test_connect_success_renders_standalone_page(client):
    """A fresh voucher activation returns the self-contained success page
    directly (200, not a redirect), with no base.html chrome or /static/
    assets — captive-portal popups may not be able to make a second request
    once the voucher grants the device internet access."""
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    username = "connectstandalone"
    password = "C0nnect#Standalone"

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM vouchers WHERE code = 'STANDAL1'")
        db.execute("DELETE FROM users WHERE username = ?", (username,))
        db.execute("DELETE FROM levels WHERE level_number = 996")
        db.execute("INSERT INTO levels (level_number, name) VALUES (996, 'Connect Page Test')")
        level_id = db.execute(
            "SELECT id FROM levels WHERE level_number = 996"
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
            ("Connect Student", username, f"{username}@cybersafe.local",
             generate_password_hash(password)),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()["id"]
        db.execute(
            "INSERT INTO vouchers (user_id, level_id, code) VALUES (?, ?, 'STANDAL1')",
            (user_id, level_id),
        )
        db.commit()

    try:
        client.post("/login", data={"identifier": username, "step": "1"})
        client.post("/login", data={"identifier": username, "step": "2", "password": password})

        response = client.post("/levels/996/connect", data={"voucher_code": "STANDAL1"})

        assert response.status_code == 200
        assert b"Connected!" in response.data
        # Self-contained: no external stylesheet and none of base.html's chrome.
        assert b"/static/css/style.css" not in response.data
        assert b"navbar" not in response.data
        # Links out of the popup are absolute and anchored at the configured
        # portal base URL — the request's Host header is untrustworthy here.
        base = app.config["PORTAL_BASE_URL"].encode()
        assert base + b"/dashboard" in response.data
        assert base + b"/internet-access" in response.data
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM vouchers WHERE code = 'STANDAL1'")
            db.execute("DELETE FROM users WHERE username = ?", (username,))
            db.execute("DELETE FROM levels WHERE level_number = 996")
            db.commit()


def test_connect_success_links_ignore_probe_host_header(client):
    """Requests arriving via the Pi's captive-portal NAT redirect carry the
    Host header of the OS connectivity probe (e.g. msftconnecttest.com), not
    the Pi's address. Links on the success page must be built from
    PORTAL_BASE_URL, not the request's Host."""
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    username = "connecthostfake"
    password = "H0st#FakeProbe"

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM vouchers WHERE code = 'HOSTFAKE'")
        db.execute("DELETE FROM users WHERE username = ?", (username,))
        db.execute("DELETE FROM levels WHERE level_number = 995")
        db.execute("INSERT INTO levels (level_number, name) VALUES (995, 'Host Header Test')")
        level_id = db.execute(
            "SELECT id FROM levels WHERE level_number = 995"
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
            ("Host Probe Student", username, f"{username}@cybersafe.local",
             generate_password_hash(password)),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()["id"]
        db.execute(
            "INSERT INTO vouchers (user_id, level_id, code) VALUES (?, ?, 'HOSTFAKE')",
            (user_id, level_id),
        )
        db.commit()

    try:
        # Simulate the NAT-redirected captive-portal case: every request in
        # the popup's session carries the OS probe's fake Host header, not
        # the Pi's address.
        probe_host = {"Host": "msftconnecttest.com"}
        client.post("/login", data={"identifier": username, "step": "1"}, headers=probe_host)
        client.post(
            "/login",
            data={"identifier": username, "step": "2", "password": password},
            headers=probe_host,
        )

        response = client.post(
            "/levels/995/connect",
            data={"voucher_code": "HOSTFAKE"},
            headers=probe_host,
        )

        assert response.status_code == 200
        base = app.config["PORTAL_BASE_URL"].encode()
        assert base + b"/dashboard" in response.data
        assert base + b"/internet-access" in response.data
        assert b"msftconnecttest.com" not in response.data
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM vouchers WHERE code = 'HOSTFAKE'")
            db.execute("DELETE FROM users WHERE username = ?", (username,))
            db.execute("DELETE FROM levels WHERE level_number = 995")
            db.commit()


# ---------- Probe-host redirect to the real portal address ----------

def test_get_with_probe_host_redirects_to_real_host(client):
    """A GET arriving under an OS captive-portal probe's fake Host — which is
    what the Pi's NAT redirect produces for unauthenticated devices — must
    get a real 302 to the configured portal address so the browser's address
    bar updates instead of staying on the fake host."""
    response = client.get("/", headers={"Host": "msftconnecttest.com"})
    assert response.status_code == 302
    assert response.headers["Location"] == (
        client.application.config["PORTAL_BASE_URL"] + "/"
    )


def test_get_with_real_host_serves_normally(client):
    """Requests already addressed to the real portal host are not redirected."""
    from urllib.parse import urlsplit
    real_host = urlsplit(client.application.config["PORTAL_BASE_URL"]).netloc
    response = client.get("/", headers={"Host": real_host})
    assert response.status_code == 200
    assert b"Learn Cybersecurity" in response.data


def test_post_with_probe_host_is_not_redirected(client):
    """POSTs are never redirected — a 302 risks the browser dropping the body
    or converting it to a GET."""
    response = client.post(
        "/login",
        data={"identifier": "nobody", "step": "1"},
        headers={"Host": "msftconnecttest.com"},
    )
    assert response.status_code == 200


def test_get_unknown_path_with_probe_host_redirects_to_real_host(client):
    """The before_request redirect fires before the 404 handler runs, so a
    probe path like Android's /generate_204 gets the absolute redirect to the
    real host — not the 404 handler's relative redirect to the landing page."""
    response = client.get("/generate_204", headers={"Host": "msftconnecttest.com"})
    assert response.status_code == 302
    assert response.headers["Location"] == (
        client.application.config["PORTAL_BASE_URL"] + "/generate_204"
    )


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
