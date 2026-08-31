"""Password policy for Cyber-S.A.F.E. accounts.

Kept in its own module so the same rules apply anywhere a password gets set
(signup today, admin password changes or resets later) rather than being
buried in one view.

The policy has three jobs:

1. Force a mix of character classes, so the password uses letters, numbers,
   and symbols.
2. Reject guessable passwords — breach-list favourites, keyboard walks, and
   repeated characters — even when they technically satisfy rule 1.
3. Reject anything derived from the account holder's own details, which are
   the first things an attacker who knows the student would try.
"""

import re

MIN_LENGTH = 10
# Werkzeug hashes the whole string, but an unbounded password is a cheap way to
# burn CPU on the Pi, so cap it.
MAX_LENGTH = 128

# Comparisons happen on a leetspeak-folded form, so these entries also cover
# variants like "P@ssw0rd" and "Adm1n".
_COMMON_PASSWORDS = {
    "password", "pass", "passcode", "letmein", "welcome", "changeme", "default",
    "secret", "qwerty", "qwertyuiop", "asdfgh", "asdfghjkl", "zxcvbn", "azerty",
    "abcabc", "abcdef", "iloveyou", "monkey", "dragon", "sunshine", "princess",
    "football", "basketball", "baseball", "soccer", "hockey", "starwars",
    "pokemon", "superman", "batman", "shadow", "master", "login", "admin",
    "administrator", "root", "toor", "guest", "user", "test", "testing",
    "trustno", "whatever", "freedom", "hello", "hellothere", "computer",
    "internet", "wifi", "google", "facebook", "youtube", "tiktok",
    "cybersafe", "cyber", "safe", "school", "student", "teacher", "grade",
    "philippines", "pilipinas", "manila", "mahalkita",
}

# Folding digits and punctuation back to letters means the blocklist does not
# need an entry per substitution.
_LEET_MAP = {
    "@": "a", "4": "a", "8": "b", "(": "c", "3": "e", "6": "g", "9": "g",
    "1": "i", "!": "i", "|": "i", "0": "o", "5": "s", "$": "s", "7": "t",
    "+": "t", "2": "z",
}

# Rows and runs an attacker's wordlist walks first.
_WALKS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1qaz2wsx",
          "abcdefghijklmnopqrstuvwxyz", "0123456789")
_WALK_LENGTH = 4

# Any blocklist entry at least this long is also rejected as a substring, so
# "Password123!" fails rather than only a bare "password".
_SUBSTRING_MIN = 5

# "Aa1!Aa1!Aa1!" satisfies every character-class rule while being trivial to
# guess, so the password also has to draw on a minimum spread of characters.
_MIN_DISTINCT = 6


def _build_walks():
    runs = set()
    for row in _WALKS:
        for direction in (row, row[::-1]):
            for start in range(len(direction) - _WALK_LENGTH + 1):
                runs.add(direction[start:start + _WALK_LENGTH])
    return frozenset(runs)


_WALK_RUNS = _build_walks()


def _is_periodic(password):
    """True when the password is one short block repeated, e.g. 'Aa1!Aa1!Aa1!'."""
    length = len(password)
    for unit in range(1, length // 2 + 1):
        if length % unit == 0 and password == password[:unit] * (length // unit):
            return True
    return False


def _normalise(value):
    """Fold to lowercase, undo leetspeak, and drop separators.

    'P@ssw0rd-123' and 'password123' both collapse to a comparable form.
    """
    lowered = value.lower()
    for source, target in _LEET_MAP.items():
        lowered = lowered.replace(source, target)
    return re.sub(r"[^a-z0-9]", "", lowered)


def personal_terms(*values):
    """Split names, usernames, and emails into words worth blocking.

    'juan.dela.cruz@school.edu' yields {'juan', 'dela', 'cruz'}. The email
    domain is dropped on purpose: shared fragments like 'com' or 'gmail' say
    nothing about the user and would reject perfectly good passwords. Words
    under three characters are skipped for the same reason.
    """
    terms = set()
    for value in values:
        if not value:
            continue
        local_part = str(value).split("@")[0]
        for word in re.split(r"[^A-Za-z0-9]+", local_part):
            if len(word) >= 3:
                terms.add(word.lower())
    return terms


def validate_password(password, personal_values=()):
    """Return a list of reasons the password is unacceptable.

    An empty list means it passed. `personal_values` should be the account's
    own details (full name, username, email) so the password cannot be built
    out of them.
    """
    problems = []

    if len(password) < MIN_LENGTH:
        problems.append(f"be at least {MIN_LENGTH} characters long")
    if len(password) > MAX_LENGTH:
        problems.append(f"be no longer than {MAX_LENGTH} characters")
    if password != password.strip():
        problems.append("not start or end with a space")

    if not re.search(r"[a-z]", password):
        problems.append("include a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("include an uppercase letter")
    if not re.search(r"[0-9]", password):
        problems.append("include a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("include a symbol, such as ! ? @ # $ % or *")

    normalised = _normalise(password)
    lowered = password.lower()

    # A trailing counter is the most common way of dressing up a weak base word.
    base_word = re.sub(r"\d+$", "", normalised)

    if any(
        candidate in _COMMON_PASSWORDS
        for candidate in (normalised, base_word)
    ) or any(
        common in normalised
        for common in _COMMON_PASSWORDS
        if len(common) >= _SUBSTRING_MIN
    ):
        problems.append("not be based on a common word that appears in password-guessing lists")

    if re.search(r"(.)\1{2,}", password):
        problems.append("not repeat the same character three times in a row")

    if password and (_is_periodic(password) or len(set(password)) < _MIN_DISTINCT):
        problems.append(f"use at least {_MIN_DISTINCT} different characters "
                        "instead of repeating a short pattern")

    if any(run in lowered for run in _WALK_RUNS):
        problems.append("not contain a run like '1234', 'abcd', or 'qwer'")

    for term in personal_terms(*personal_values):
        if _normalise(term) and _normalise(term) in normalised:
            problems.append("not contain your name, username, or email")
            break

    return problems


def describe_problems(problems):
    """Turn `validate_password` output into one sentence for a flash message.

    Flash messages render as click-through modals, so all the reasons need to
    arrive in a single string rather than one flash each.
    """
    if not problems:
        return None
    if len(problems) == 1:
        return f"Your password must {problems[0]}."
    joined = "; ".join(problems[:-1])
    return f"Your password must {joined}; and {problems[-1]}."
