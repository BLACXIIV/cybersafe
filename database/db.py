import sqlite3
import os
import re
from flask import g, current_app
from werkzeug.security import generate_password_hash


def get_db():
    """Return a SQLite connection stored on Flask's request context `g`."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    """Create the database file + tables from schema.sql if they don't exist."""
    os.makedirs(os.path.dirname(app.config["DATABASE_PATH"]), exist_ok=True)
    with app.app_context():
        db = get_db()
        with open(app.config["SCHEMA_PATH"], "r") as f:
            db.executescript(f.read())
        db.commit()


def register_app(app):
    """Wire up teardown handling and expose a CLI command: `flask init-db`."""
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command():
        init_db(app)
        print("Initialized the database.")


def _normalize_grade_name(name):
    name = (name or "").strip()
    digits = ""
    for ch in name:
        if ch.isdigit():
            digits += ch
        elif digits and not ch.isspace():
            break
    if digits:
        return f"Grade {digits}"
    return name


def _dedupe_grades(db):
    """Normalize grade names and merge duplicates. Update users.grade_section to match."""
    grades = db.execute("SELECT id, name FROM grades").fetchall()
    name_to_id = {}
    for g in grades:
        norm = _normalize_grade_name(g["name"])
        if norm in name_to_id:
            old_id = g["id"]
            db.execute(
                "UPDATE users SET grade_section = ? WHERE grade_section = ?",
                (norm, g["name"]),
            )
            db.execute("DELETE FROM grades WHERE id = ?", (old_id,))
        else:
            name_to_id[norm] = g["id"]
            db.execute("UPDATE grades SET name = ? WHERE id = ?", (norm, g["id"]))
            db.execute(
                "UPDATE users SET grade_section = ? WHERE grade_section = ?",
                (norm, g["name"]),
            )


def _ensure_user_answers_claimed_column(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(user_answers)").fetchall()}
    if "claimed" not in columns:
        db.execute("ALTER TABLE user_answers ADD COLUMN claimed INTEGER NOT NULL DEFAULT 0")
        # Existing answers were already credited under the old system, so mark them claimed.
        db.execute("UPDATE user_answers SET claimed = 1")


def ensure_admin_data(app):
    """Add admin tables and the initial admin account without resetting data."""
    database_path = app.config["DATABASE_PATH"]
    if not os.path.exists(database_path):
        return

    with app.app_context():
        db = get_db()
        _ensure_user_answers_claimed_column(db)

        columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "role" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
        if "is_active" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "cooldown_until" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN cooldown_until TIMESTAMP")
        if "is_password_set" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN is_password_set INTEGER NOT NULL DEFAULT 1")

        voucher_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "vouchers" in voucher_tables:
            voucher_columns = {row[1] for row in db.execute("PRAGMA table_info(vouchers)").fetchall()}
            if "ip_address" not in voucher_columns:
                db.execute("ALTER TABLE vouchers ADD COLUMN ip_address TEXT")
            if "mac_address" not in voucher_columns:
                db.execute("ALTER TABLE vouchers ADD COLUMN mac_address TEXT")

        db.execute("""CREATE TABLE IF NOT EXISTS school_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            school_name TEXT NOT NULL DEFAULT 'Cyber-S.A.F.E. School',
            logo_path TEXT
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS site_visits (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            domain     TEXT NOT NULL,
            visited_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_site_visits_user ON site_visits(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_site_visits_domain ON site_visits(domain)")
        db.execute(
            "INSERT OR IGNORE INTO school_settings (id, school_name) VALUES (1, ?)",
            ("Cyber-S.A.F.E. School",),
        )
        # Admin account uses a fixed LRN so the admin logs in through the same
        # two-step student flow.
        admin_lrn = "123456789012"
        admin_email = f"{admin_lrn}@cybersafe.local"
        admin_password = generate_password_hash("admin")

        existing_admin = db.execute(
            "SELECT id FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()
        if existing_admin:
            db.execute(
                "UPDATE users SET username = ?, email = ?, password_hash = ?, is_password_set = 1 WHERE id = ?",
                (admin_lrn, admin_email, admin_password, existing_admin["id"]),
            )
        else:
            db.execute(
                """INSERT OR IGNORE INTO users
                   (full_name, username, email, password_hash, role, is_password_set)
                   VALUES (?, ?, ?, ?, 'admin', 1)""",
                ("Administrator", admin_lrn, admin_email, admin_password),
            )
        db.execute(
            "UPDATE users SET role = 'admin' WHERE username = ?",
            (admin_lrn,),
        )
        db.execute("UPDATE users SET is_password_set = 1 WHERE is_password_set IS NULL")
        _dedupe_grades(db)
        db.commit()
