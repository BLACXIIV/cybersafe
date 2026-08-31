import pytest
from app import create_app
from security import MIN_LENGTH, validate_password, describe_problems


@pytest.fixture
def app():
    app = create_app()
    app.config.update({"TESTING": True})
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


def test_signup_rejects_weak_password(client):
    response = client.post("/signup", data={
        "full_name": "Test Student",
        "username": "weakpwuser",
        "email": "weakpwuser@school.edu",
        "grade_id": "1",
        "section_id": "1",
        "password": "password123",
        "confirm_password": "password123",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Your password must" in response.data


def test_signup_page_shows_the_rules(client):
    response = client.get("/signup")
    assert b"pw-rules" in response.data
    assert f"At least {MIN_LENGTH} characters".encode() in response.data
