import sqlite3
import os
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
        db.execute("""CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade_id INTEGER NOT NULL REFERENCES grades(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(grade_id, name)
        )""")
        db.execute(
            "INSERT OR IGNORE INTO school_settings (id, school_name) VALUES (1, ?)",
            ("Cyber-S.A.F.E. School",),
        )
        db.execute(
            """INSERT OR IGNORE INTO users
               (full_name, username, email, password_hash, role)
               VALUES (?, ?, ?, ?, 'admin')""",
            ("Administrator", "admin", "admin@cybersafe.local", generate_password_hash("admin")),
        )
        db.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")
        db.commit()
