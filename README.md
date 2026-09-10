# Cyber-S.A.F.E.

Cyber-S.A.F.E. — Development of an Adaptive Gamified Cybersecurity Awareness Gateway System with Voucher-Based Internet Access Authorization for Junior High School Students.

A Flask + SQLite web application for student cybersecurity training. The platform uses LRN-based accounts, randomized mission questions, points, badge ranks, and voucher-gated internet access.

## Features

### Students
- **LRN-based login**: 12-digit Learner Reference Number (LRN) entry with OTP-style boxes.
- **First-time login**: if no password exists, the student must create a strong password with a live strength indicator.
- **Normal login**: LRN + password for returning students.
- **Missions/levels**: randomized cybersecurity questions per mission with immediate feedback and a short cooldown after incorrect answers.
- **Points & badges**: progress through Bronze, Silver, Gold, and Platinum ranks.
- **Vouchers**: earn internet-access vouchers by answering mission questions correctly; each active voucher lasts **1 hour** and can be toggled on/off from the dashboard.

### Admin
- **School branding**: update school name and logo.
- **Student management**: register individual students (LRN, first name, surname, grade level) or bulk-import from an Excel file.
- **Grade management**: add/edit grade levels.
- **Rankings & reports**: view student point rankings and a susceptibility/readiness pie chart.
- **Password reset**: reset a student password and force a new password on next login.

### Technical
- **Responsive UI**: mobile-first CSS with breakpoints for tablet and desktop.
- **Rate limiting**: Flask-Limiter with per-IP and per-user limits and a custom 429 page.
- **Password policy**: enforced server-side; checks length, case, digits, symbols, common/keyboard patterns, and personal information.
- **Network integration**: optional Pi-based firewall gating via `network/cybersafe-grant-access` (see `NETWORK_SETUP.md`).
- **Tests**: pytest suite in `tests/test_app.py`.

## Project structure

```
cybersafe/
├── app.py                  # Application factory / entry point
├── auth.py                 # Login, logout, two-step LRN authentication
├── main.py                 # Landing page, dashboard, internet access, vouchers
├── levels.py               # Mission/level questions, voucher activation
├── admin.py                # Admin dashboard, reports, student import/reset
├── security.py             # Password validation and policy helpers
├── ranks.py                # Badge/rank thresholds
├── network_access.py       # Bridge to the Pi firewall helper
├── config.py               # Configuration (DB path, secret key, etc.)
├── extensions.py           # Shared Flask extensions (limiter)
├── requirements.txt        # Python dependencies
├── database/
│   ├── db.py               # SQLite helpers and `flask init-db`
│   ├── schema.sql          # Database schema
│   └── seed_questions.py   # Sample mission questions
├── network/                # Pi captive-portal helpers
│   ├── cybersafe-grant-access
│   ├── reconcile_access.py
│   ├── setup_ap.sh
│   └── systemd/
├── tests/
│   └── test_app.py         # pytest suite
├── templates/              # Jinja2 templates
└── static/
    ├── css/style.css
    ├── css/hero.css
    └── js/password-strength.js
```

## Setup

```bash
cd cybersafe
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
python app.py
```

The first run auto-creates `database/cybersafe.db` from `schema.sql` and seeds the admin account.

Open **http://127.0.0.1:5000**.

## Administrator login

Use the same login page at **/login**. The first account with the `admin` role is created automatically on startup; change the default administrator password before deploying this application.

## Running tests

```bash
pytest -q
```

## Database reset

```bash
rm database/cybersafe.db
flask --app app init-db
```

## Security notes

- Passwords are hashed with `werkzeug.security.generate_password_hash`; plain-text passwords are never stored.
- `SECRET_KEY` in `config.py` is a development placeholder. Set a real key via the `CYBERSAFE_SECRET_KEY` environment variable before any real deployment.
- Rate limiting is active in production; the test fixture disables it to avoid test pollution.
- For the Pi captive-portal network setup, see `NETWORK_SETUP.md`.
