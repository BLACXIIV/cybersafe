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


# ---------- Reconnect handling ----------

def _login_admin(client):
    """The seeded admin logs in through the same two-step LRN flow."""
    client.post("/login", data={"identifier": "123456789012", "step": "1"})
    client.post(
        "/login",
        data={"identifier": "123456789012", "step": "2", "password": "admin"},
    )


def test_probe_host_reconnect_keeps_session(client):
    """A request arriving under an OS captive-portal probe's fake Host gets
    redirected to the real portal address, but must NOT log the device out:
    phones emit foreign-Host HTTP traffic constantly (all port-80 traffic is
    DNAT'd to the app while the device is unvouchered), so treating it as a
    reconnect kicked logged-in students mid-session."""
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    username = "reconnectkeep"
    password = "R3connect#Keep"

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM users WHERE username = ?", (username,))
        db.execute(
            """INSERT INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
            ("Reconnect Student", username, f"{username}@cybersafe.local",
             generate_password_hash(password)),
        )
        db.commit()

    try:
        client.post("/login", data={"identifier": username, "step": "1"})
        client.post("/login", data={"identifier": username, "step": "2", "password": password})
        assert client.get("/dashboard").status_code == 200

        # Fake-host probe: still redirected to the real portal address.
        response = client.get("/", headers={"Host": "msftconnecttest.com"})
        assert response.status_code == 302
        assert response.headers["Location"] == (
            app.config["PORTAL_BASE_URL"] + "/"
        )

        # Back on the real host (same client, same cookie jar), the session
        # is untouched — the dashboard still renders.
        assert client.get("/dashboard").status_code == 200
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM users WHERE username = ?", (username,))
            db.commit()


def test_normal_browsing_does_not_clear_session(client):
    """Ordinary navigation on the real host must not touch the session."""
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    username = "keepmesignedin"
    password = "K33p#SignedIn"

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM users WHERE username = ?", (username,))
        db.execute(
            """INSERT INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
            ("Browsing Student", username, f"{username}@cybersafe.local",
             generate_password_hash(password)),
        )
        db.commit()

    try:
        client.post("/login", data={"identifier": username, "step": "1"})
        client.post("/login", data={"identifier": username, "step": "2", "password": password})
        assert client.get("/dashboard").status_code == 200

        # Ordinary navigation on the real host to a different page.
        assert client.get("/leaderboard").status_code == 200

        # Still logged in afterwards.
        assert client.get("/dashboard").status_code == 200
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM users WHERE username = ?", (username,))
            db.commit()


def test_reconnect_during_login_does_not_expire_session(client):
    """A WiFi blip between login step 1 (LRN) and step 2 (password) must not
    wipe the transient login markers — mobile devices reconnect silently on
    screen lock/backgrounding, mid-login."""
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    username = "midloginblip"
    password = "M1dLogin#Blip"

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM users WHERE username = ?", (username,))
        db.execute(
            """INSERT INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
            ("Mid Login", username, f"{username}@cybersafe.local",
             generate_password_hash(password)),
        )
        db.commit()

    try:
        # Step 1 stores login_lrn/login_user_id and renders the password form.
        assert client.post(
            "/login", data={"identifier": username, "step": "1"}
        ).status_code == 200

        # WiFi blip mid-login: fake-host probe, then the first real-host
        # request it triggers.
        assert client.get(
            "/", headers={"Host": "msftconnecttest.com"}
        ).status_code == 302
        assert client.get("/").status_code == 200

        # Step 2 still finds the markers and completes the login — before the
        # fix this redirected to /login with "Session expired".
        response = client.post(
            "/login",
            data={"identifier": username, "step": "2", "password": password},
        )
        assert response.status_code == 302
        assert "/dashboard" in response.headers["Location"]
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM users WHERE username = ?", (username,))
            db.commit()


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


# ---------- Site-visit logging ----------

def test_admin_activity_scopes_visits_to_the_right_student(client):
    """Visits recorded against each voucher holder render under that
    student's name, most recent first, and honor the period filter.

    Assertions tolerate unrelated rows already in site_visits: rows are
    matched by test-marker domains, never by absolute position."""
    import re
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM site_visits WHERE domain LIKE '%.activity.test'")
        db.execute("DELETE FROM vouchers WHERE code IN ('ACTV001', 'ACTV002')")
        db.execute("DELETE FROM users WHERE username IN ('activityone', 'activitytwo')")
        db.execute("DELETE FROM levels WHERE level_number = 994")
        db.execute("INSERT INTO levels (level_number, name) VALUES (994, 'Activity Test')")
        level_id = db.execute(
            "SELECT id FROM levels WHERE level_number = 994"
        ).fetchone()["id"]
        user_ids = {}
        for username, full_name, ip in (
            ("activityone", "Ana Reyes", "10.42.0.101"),
            ("activitytwo", "Jose Ramos", "10.42.0.102"),
        ):
            db.execute(
                """INSERT INTO users
                   (full_name, username, email, password_hash, grade_section, role, is_password_set)
                   VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
                (full_name, username, f"{username}@cybersafe.local",
                 generate_password_hash("Act1vity#Test")),
            )
            user_ids[username] = db.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()["id"]

        ana, jose = user_ids["activityone"], user_ids["activitytwo"]
        db.execute(
            """INSERT INTO vouchers (user_id, level_id, code, used_at, expires_at, ip_address)
               VALUES (?, ?, 'ACTV001', CURRENT_TIMESTAMP, datetime('now', '+1 hours'), '10.42.0.101')""",
            (ana, level_id),
        )
        db.execute(
            """INSERT INTO vouchers (user_id, level_id, code, used_at, expires_at, ip_address)
               VALUES (?, ?, 'ACTV002', CURRENT_TIMESTAMP, datetime('now', '+1 hours'), '10.42.0.102')""",
            (jose, level_id),
        )
        # The three newest visits, so they land on page 1 in this order.
        for user_id, domain, when in (
            (ana, "alpha.activity.test", "-1 minutes"),
            (jose, "bravo.activity.test", "-2 minutes"),
            (ana, "charlie.activity.test", "-3 minutes"),
        ):
            db.execute(
                f"INSERT INTO site_visits (user_id, domain, visited_at) "
                f"VALUES (?, ?, datetime('now', '{when}'))",
                (user_id, domain),
            )
        # Out of "today" range; searched directly rather than paged to.
        db.execute(
            """INSERT INTO site_visits (user_id, domain, visited_at)
               VALUES (?, 'delta.activity.test', datetime('now', '-3 days'))""",
            (ana,),
        )
        # Old but numerous: guarantees a slice in the donut's top 8 without
        # disturbing the page-1 ordering checks above.
        for _ in range(60):
            db.execute(
                """INSERT INTO site_visits (user_id, domain, visited_at)
                   VALUES (?, 'pie.activity.test', datetime('now', '-4 days'))""",
                (ana,),
            )
        db.commit()

    try:
        # Not logged in -> bounced to login.
        assert client.get("/admin/activity").status_code == 302

        _login_admin(client)
        response = client.get("/admin/activity")
        assert response.status_code == 200
        data = response.data

        # Each row binds the student name to the domain inside one
        # ranking-row, so extracting pairs asserts attribution directly.
        pairs = {
            (name, domain)
            for name, _sub, domain in re.findall(
                rb'<div class="ranking-name">\s*<strong>([^<]+)</strong>\s*'
                rb'<small>([^<]*)</small>\s*</div>\s*'
                rb'<div class="ranking-stats">\s*<strong[^>]*title="([^"]+)"',
                data,
            )
        }
        assert (b"Ana Reyes", b"alpha.activity.test") in pairs
        assert (b"Ana Reyes", b"charlie.activity.test") in pairs
        assert (b"Jose Ramos", b"bravo.activity.test") in pairs
        assert (b"Jose Ramos", b"alpha.activity.test") not in pairs

        # Most recent first: alpha, bravo, charlie.
        assert (data.index(b"alpha.activity.test")
                < data.index(b"bravo.activity.test")
                < data.index(b"charlie.activity.test"))

        # The 3-day-old visit exists in All time but not under Today.
        # Rows carry title="<domain>", so match that rather than the bare
        # string (the search box echoes q back into the page).
        search = client.get("/admin/activity", query_string={"q": "delta.activity.test"})
        assert b'title="delta.activity.test"' in search.data
        today = client.get(
            "/admin/activity", query_string={"period": "today", "q": "delta.activity.test"}
        )
        assert b'title="delta.activity.test"' not in today.data
        assert b"alpha.activity.test" in client.get(
            "/admin/activity", query_string={"period": "today"}
        ).data

        # The analytics report renders the donut off the same data.
        analytics = client.get("/admin/analytics")
        assert analytics.status_code == 200
        assert b"Most visited sites" in analytics.data
        assert b"pie.activity.test" in analytics.data
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM site_visits WHERE domain LIKE '%.activity.test'")
            db.execute("DELETE FROM vouchers WHERE code IN ('ACTV001', 'ACTV002')")
            db.execute("DELETE FROM users WHERE username IN ('activityone', 'activitytwo')")
            db.execute("DELETE FROM levels WHERE level_number = 994")
            db.commit()


def test_activity_live_now_lists_active_voucher_students(client):
    """Live now lists students holding an active voucher with their current
    session: domain, running duration, start time, ongoing marker. An
    expired voucher drops the student out of the live list but keeps their
    history rows."""
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    app = client.application
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM site_sessions WHERE domain LIKE '%.live.test'")
        db.execute("DELETE FROM site_visits WHERE domain LIKE '%.live.test'")
        db.execute("DELETE FROM vouchers WHERE code IN ('LIVE001', 'LIVEEXP')")
        db.execute("DELETE FROM users WHERE username IN ('liveactive', 'liveexpired')")
        db.execute("DELETE FROM levels WHERE level_number = 993")
        db.execute("INSERT INTO levels (level_number, name) VALUES (993, 'Live Test')")
        level_id = db.execute(
            "SELECT id FROM levels WHERE level_number = 993"
        ).fetchone()["id"]
        for username, full_name in (
            ("liveactive", "Liv Active"),
            ("liveexpired", "Liv Expired"),
        ):
            db.execute(
                """INSERT INTO users
                   (full_name, username, email, password_hash, grade_section, role, is_password_set)
                   VALUES (?, ?, ?, ?, 'Grade 10', 'student', 1)""",
                (full_name, username, f"{username}@cybersafe.local",
                 generate_password_hash("L1ve#Test99")),
            )
        active_id = db.execute(
            "SELECT id FROM users WHERE username = 'liveactive'"
        ).fetchone()["id"]
        expired_id = db.execute(
            "SELECT id FROM users WHERE username = 'liveexpired'"
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO vouchers (user_id, level_id, code, used_at, expires_at, ip_address)
               VALUES (?, ?, 'LIVE001', CURRENT_TIMESTAMP, datetime('now', '+1 hours'), '10.42.0.201')""",
            (active_id, level_id),
        )
        db.execute(
            """INSERT INTO vouchers (user_id, level_id, code, used_at, expires_at, ip_address)
               VALUES (?, ?, 'LIVEEXP', CURRENT_TIMESTAMP, datetime('now', '-1 hours'), '10.42.0.202')""",
            (expired_id, level_id),
        )
        # The live row comes from site_sessions: started 10m ago, still seen
        # 30s ago -> "ongoing". History below still reads site_visits.
        db.execute(
            """INSERT INTO site_sessions (user_id, domain, started_at, last_seen_at, lookups)
               VALUES (?, 'fresh.live.test', datetime('now', '-10 minutes'), datetime('now', '-30 seconds'), 9)""",
            (active_id,),
        )
        db.execute(
            """INSERT INTO site_sessions (user_id, domain, started_at, last_seen_at, lookups)
               VALUES (?, 'gone.live.test', datetime('now', '-1 hours'), datetime('now', '-30 minutes'), 4)""",
            (expired_id,),
        )
        db.execute(
            "INSERT INTO site_visits (user_id, domain) VALUES (?, 'gone.live.test')",
            (expired_id,),
        )
        db.commit()

    try:
        _login_admin(client)
        data = client.get("/admin/activity").data

        # Only the active voucher holder appears in the live list, as an
        # ongoing session with its domain and duration.
        live_block = data.split(b'id="live_results"', 1)[1].split(b"</section>", 1)[0]
        assert b"Liv Active" in live_block
        assert b"fresh.live.test" in live_block
        assert b"ongoing" in live_block
        assert b"10m" in live_block
        assert b"Liv Expired" not in live_block

        # The expired student's visit still shows in the history below.
        assert b"gone.live.test" in data
    finally:
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM site_sessions WHERE domain LIKE '%.live.test'")
            db.execute("DELETE FROM site_visits WHERE domain LIKE '%.live.test'")
            db.execute("DELETE FROM vouchers WHERE code IN ('LIVE001', 'LIVEEXP')")
            db.execute("DELETE FROM users WHERE username IN ('liveactive', 'liveexpired')")
            db.execute("DELETE FROM levels WHERE level_number = 993")
            db.commit()


def test_log_site_visits_tracks_sessions(client):
    """Each matched DNS lookup upserts a site_sessions row: repeats inside
    the 5-minute gap extend it, a longer quiet opens a new one — while the
    sparse site_visits log still throttles to one row per minute."""
    import sqlite3
    import sys
    import os
    from werkzeug.security import generate_password_hash
    from database.db import get_db

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "network")
    )
    import log_site_visits

    app = client.application
    username = "sessstudent"
    ip = "10.42.0.211"

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM site_sessions WHERE domain = 'sess.live.test'")
        db.execute("DELETE FROM site_visits WHERE domain = 'sess.live.test'")
        db.execute("DELETE FROM vouchers WHERE code = 'SESSTST'")
        db.execute("DELETE FROM users WHERE username = ?", (username,))
        db.execute("DELETE FROM levels WHERE level_number = 992")
        db.execute("INSERT INTO levels (level_number, name) VALUES (992, 'Session Test')")
        level_id = db.execute(
            "SELECT id FROM levels WHERE level_number = 992"
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO users
               (full_name, username, email, password_hash, grade_section, role, is_password_set)
               VALUES ('Sess Student', ?, ?, ?, 'Grade 10', 'student', 1)""",
            (username, f"{username}@cybersafe.local",
             generate_password_hash("S3ss#Test99")),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO vouchers (user_id, level_id, code, used_at, expires_at, ip_address)
               VALUES (?, ?, 'SESSTST', CURRENT_TIMESTAMP, datetime('now', '+1 hours'), ?)""",
            (user_id, level_id, ip),
        )
        db.commit()

    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        def line(t):
            return (f"Sep 24 {t}:00 dnsmasq[1]: 4 {ip}/40000 "
                    f"query[A] sess.live.test from {ip}\n")

        last_logged = {}
        ok = log_site_visits._record_line(conn, line("12:00"), last_logged)
        ok = log_site_visits._record_line(conn, line("12:02"), last_logged, ok)
        ok = log_site_visits._record_line(conn, line("12:10"), last_logged, ok)
        assert ok

        rows = conn.execute(
            """SELECT started_at, last_seen_at, lookups FROM site_sessions
               WHERE user_id = ? AND domain = 'sess.live.test' ORDER BY id""",
            (user_id,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["lookups"] == 2   # 12:00 + 12:02 share one session
        assert rows[1]["lookups"] == 1   # 12:10 is past the gap

        # The sparse visit log throttled all three calls into one row.
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM site_visits "
            "WHERE user_id = ? AND domain = 'sess.live.test'",
            (user_id,),
        ).fetchone()["c"] == 1
    finally:
        conn.close()
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM site_sessions WHERE domain = 'sess.live.test'")
            db.execute("DELETE FROM site_visits WHERE domain = 'sess.live.test'")
            db.execute("DELETE FROM vouchers WHERE code = 'SESSTST'")
            db.execute("DELETE FROM users WHERE username = ?", (username,))
            db.execute("DELETE FROM levels WHERE level_number = 992")
            db.commit()


def test_record_line_degrades_to_visits_without_sessions_table(tmp_path):
    """A DB that predates the site_sessions migration must not go silent:
    the session upsert fails once, then visits keep recording."""
    import sqlite3
    import sys
    import os

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "network")
    )
    import log_site_visits

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE vouchers (
            id INTEGER PRIMARY KEY, user_id INTEGER, code TEXT,
            used_at TIMESTAMP, expires_at TIMESTAMP, ip_address TEXT)"""
    )
    conn.execute(
        """CREATE TABLE site_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            domain TEXT, visited_at TIMESTAMP)"""
    )
    conn.execute(
        """INSERT INTO vouchers (user_id, code, used_at, expires_at, ip_address)
           VALUES (7, 'OLD001', CURRENT_TIMESTAMP, datetime('now', '+1 hours'), '10.42.0.99')"""
    )
    conn.commit()

    line = ("Sep 24 12:00:00 dnsmasq[1]: 4 10.42.0.99/40000 "
            "query[A] degraded.live.test from 10.42.0.99\n")
    last_logged = {}
    ok = log_site_visits._record_line(conn, line, last_logged)
    assert ok is False  # sessions unusable, flagged for the rest of the run
    ok = log_site_visits._record_line(conn, line, last_logged, ok)
    assert ok is False
    assert conn.execute("SELECT COUNT(*) AS c FROM site_visits").fetchone()["c"] == 1
    conn.close()


def test_ensure_schema_creates_site_sessions(tmp_path):
    """The daemon creates the table itself on DBs that predate the
    migration — the safety net for a not-yet-restarted app."""
    import sqlite3
    import sys
    import os

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "network")
    )
    import log_site_visits

    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    assert log_site_visits._ensure_schema(conn)
    conn.execute(
        "INSERT INTO site_sessions (user_id, domain, started_at, last_seen_at) "
        "VALUES (1, 'x.test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    conn.close()


def test_top_domains_groups_limits_and_buckets(client):
    """The donut data keeps the top 8 domains and folds the rest into Other.

    Runs inside a never-committed transaction: the wipe + fixture rows are
    only visible to this connection and rolled back afterwards, so real
    site_visits data survives the test."""
    from datetime import datetime, timedelta
    from admin import TOP_DOMAIN_LIMIT, _top_domains
    from database.db import get_db

    app = client.application
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM users WHERE username = 'topdomains'")
        db.commit()
        try:
            db.execute("DELETE FROM site_visits")
            db.execute(
                """INSERT INTO users
                   (full_name, username, email, password_hash, role, is_password_set)
                   VALUES ('Top Domains', 'topdomains', 'topdomains@cybersafe.local', '', 'student', 1)"""
            )
            user_id = db.execute(
                "SELECT id FROM users WHERE username = 'topdomains'"
            ).fetchone()["id"]
            # site01 gets 10 visits, site02 gets 9, ... site10 gets 1.
            for i in range(10):
                for _ in range(10 - i):
                    db.execute(
                        "INSERT INTO site_visits (user_id, domain) VALUES (?, ?)",
                        (user_id, f"site{i + 1:02d}.topdomains.test"),
                    )
            db.execute(
                """INSERT INTO site_visits (user_id, domain, visited_at)
                   VALUES (?, 'stale.topdomains.test', datetime('now', '-3 days'))""",
                (user_id,),
            )

            total = 56
            slices = _top_domains(db, None)
            assert len(slices) == TOP_DOMAIN_LIMIT + 1
            assert [s["domain"] for s in slices[:3]] == [
                "site01.topdomains.test",
                "site02.topdomains.test",
                "site03.topdomains.test",
            ]
            assert [s["visits"] for s in slices[:3]] == [10, 9, 8]
            for i, s in enumerate(slices[:TOP_DOMAIN_LIMIT]):
                expected = (10 - i) / total
                assert s["pct"] == round(expected * 100, 1)
                assert s["color"]

            # Everything past the top 8 folds into Other (site09's 2 +
            # site10's 1 + the stale row's 1).
            assert slices[-1]["domain"] == "Other"
            assert slices[-1]["visits"] == 4
            assert slices[-1]["pct"] == round(4 / total * 100, 1)

            # A period start excludes the stale row entirely.
            recent = _top_domains(db, datetime.utcnow() - timedelta(hours=1))
            domains = [s["domain"] for s in recent]
            assert "stale.topdomains.test" not in domains
            assert recent[-1]["domain"] == "Other" and recent[-1]["visits"] == 3
        finally:
            db.rollback()
