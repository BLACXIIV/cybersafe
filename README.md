# Cyber-S.A.F.E. — Base Code (Flask + SQLite)

A responsive starter for the Cyber-S.A.F.E. system: sign up, log in, and a
landing/dashboard page. Built so mission logic, adaptive questions, badges,
and the captive-portal/voucher system can be layered on top later without
restructuring the auth code.

## Project structure

```
cybersafe/
├── app.py                  # Entry point / app factory
├── auth.py                 # Signup, login, logout, login_required decorator
├── main.py                 # Landing page + dashboard routes
├── config.py                # App config (secret key, DB path)
├── requirements.txt
├── database/
│   ├── db.py                # SQLite connection helpers + `flask init-db`
│   └── schema.sql           # users table (extend with missions, questions, etc.)
├── templates/
│   ├── base.html            # Shared layout, navbar, flash messages
│   ├── index.html           # Landing page (logged out)
│   ├── signup.html
│   ├── login.html
│   └── dashboard.html       # Landing page (logged in)
└── static/
    └── css/style.css        # Mobile-first responsive styling
```

## Setup

```bash
cd cybersafe
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The first run auto-creates `database/cybersafe.db` from `schema.sql`.
Then open **http://127.0.0.1:5000**.

### Administrator

Use the same login page at **/login** with username `admin` and password
`admin`. Successful administrator login redirects to **/admin/**, where you
can update the school name and logo, manage grade/section options, and view
the student points ranking.

Change the default administrator password before deploying this application.

To reset the database at any point:

```bash
rm database/cybersafe.db
flask --app app init-db
```

## What's included

- **Signup** (`/signup`) — full name, username, email, grade/section, password
  (hashed with Werkzeug's `generate_password_hash`, never stored in plain text).
- **Login** (`/login`) — by username or email, session-based auth.
- **Logout** (`/logout`).
- **Landing page** (`/`) — marketing/intro view for logged-out visitors,
  automatically swaps to the dashboard once logged in.
- **Dashboard** (`/dashboard`) — protected route (`@login_required`), shows
  points, level, and a placeholder mission list ready to be wired to real data.
- **Responsive layout** — mobile-first CSS with breakpoints at 640px (tablet)
  and 1024px (laptop/desktop): forms stack on phones and go side-by-side on
  larger screens, the stat grid goes from 2 columns → 4, feature cards go
  1 → 2 → 3 columns.

## Security notes for your write-up

- Passwords are hashed (`werkzeug.security`), never stored raw.
- `SECRET_KEY` in `config.py` is a dev placeholder — set a real one via the
  `CYBERSAFE_SECRET_KEY` environment variable before any real deployment.
- Password policy lives in `security.py` and is enforced server-side on signup:
  minimum 10 characters, must mix lowercase, uppercase, numbers, and symbols,
  and is rejected if it matches a common-password blocklist (leetspeak-folded,
  so `P@ssw0rd` is caught), contains a keyboard walk or run like `qwer`/`1234`,
  repeats a short pattern, or is built from the user's own name, username, or
  email. The signup page mirrors the same rules as a live checklist, but the
  server check is authoritative.
- Still outstanding: rate limiting and CSRF protection (e.g. `Flask-WTF`)
  before this touches a real school network.

## Suggested next steps

1. Add `missions`, `questions`, and `attempts` tables to `schema.sql`.
2. Build a `missions.py` blueprint with the adaptive question-selection logic.
3. Add a `vouchers` table + generation logic for the Internet-access clearance step.
4. Add an `admin.py` blueprint gated by a `role` column on `users`.
5. Swap the placeholder progress/mission data in `dashboard.html` for real
   queries once the missions table exists.
