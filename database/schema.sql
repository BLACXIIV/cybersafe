-- Cyber-S.A.F.E. database schema
-- Users + the mission/question/attempt system. Vouchers and badges can be
-- added here later without touching the auth or quiz-engine code.

DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS levels;
DROP TABLE IF EXISTS questions;
DROP TABLE IF EXISTS choices;
DROP TABLE IF EXISTS user_answers;
DROP TABLE IF EXISTS user_level_progress;

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    grade_section TEXT,
    role          TEXT NOT NULL DEFAULT 'student',
    points        INTEGER NOT NULL DEFAULT 0,
    level         TEXT NOT NULL DEFAULT 'Cyber Rookie',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A "level" here is a mission tier (Level 1 Beginner, Level 2 Intermediate,
-- Level 3 Advanced), each holding a bank of questions students draw from.
CREATE TABLE levels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    level_number INTEGER NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    focus        TEXT
);

CREATE TABLE questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    level_id        INTEGER NOT NULL REFERENCES levels(id),
    question_number INTEGER NOT NULL,
    prompt          TEXT NOT NULL,
    explanation     TEXT
);

-- Each choice carries its own point value (0/25/50/100) rather than a
-- simple is_correct flag, matching the weighted scoring in the source
-- questionnaire. The choice worth 100 points is treated as "correct" by
-- the app, but partial-credit values are preserved for scoring/analytics.
CREATE TABLE choices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    letter      TEXT NOT NULL,      -- 'A', 'B', 'C', 'D'
    choice_text TEXT NOT NULL,
    points      INTEGER NOT NULL
);

CREATE TABLE user_answers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    question_id   INTEGER NOT NULL REFERENCES questions(id),
    choice_id     INTEGER NOT NULL REFERENCES choices(id),
    points_earned INTEGER NOT NULL,
    claimed       INTEGER NOT NULL DEFAULT 0,  -- 1 once this answer's points are added to users.points
    answered_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Tracks whether a student has cleared each level (all questions attempted
-- with the correct/100-point answer, per the mastery rule the app enforces).
CREATE TABLE user_level_progress (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    level_id     INTEGER NOT NULL REFERENCES levels(id),
    status       TEXT NOT NULL DEFAULT 'locked',  -- locked | in_progress | mastered
    total_points INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, level_id)
);

-- Vouchers earned by students for verified, non-zero mission performance.
-- One code per user/level; the code is revealed on the access-verification page.
CREATE TABLE vouchers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    level_id   INTEGER NOT NULL REFERENCES levels(id),
    code       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    used_at    TIMESTAMP,
    expires_at TIMESTAMP,
    UNIQUE(user_id, level_id)
);

CREATE TABLE school_settings (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    school_name TEXT NOT NULL DEFAULT 'Cyber-S.A.F.E. School',
    logo_path   TEXT
);

CREATE TABLE grades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    grade_id   INTEGER NOT NULL REFERENCES grades(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(grade_id, name)
);

INSERT OR IGNORE INTO school_settings (id, school_name) VALUES (1, 'Cyber-S.A.F.E. School');
