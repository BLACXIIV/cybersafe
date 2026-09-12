"""Smoke test: log in as admin, hit every admin screen + report variants + exports."""
from app import create_app

app = create_app()
app.config.update({"TESTING": True, "RATELIMIT_ENABLED": False})
client = app.test_client()

# Two-step login as admin.
r = client.post("/login", data={"identifier": "123456789012", "step": "1"})
r = client.post("/login", data={"step": "2", "password": "admin"})
print("login:", r.status_code, r.headers.get("Location"))

checks = [
    ("/admin/", [302]),
    ("/admin/students", [200, b"Student roster", b"admin-tab", b"hero-showcase", b"Register students"]),
    ("/admin/missions", [200, b"Mission bank", b"admin-tab", b"hero-showcase", b"Question bank"]),
    ("/admin/settings", [200, b"School branding", b"Grade levels", b"hero-showcase"]),
    ("/admin/report", [200, b"Badge distribution", b"Susceptibility by grade", b"Weakest missions", b"Most-missed questions", b"period-chip", b"Never started"]),
    ("/admin/report?period=week", [200, b"This week"]),
    ("/admin/report?period=today&grade=Grade+10", [200]),
    ("/admin/report/export?format=csv", [200]),
    ("/admin/report/export?format=xlsx&period=month", [200]),
]

ok = True
for path, expected in checks:
    r = client.get(path)
    status_ok = r.status_code == expected[0]
    markers = expected[1:]
    missing = [m for m in markers if m not in r.data]
    state = "OK " if status_ok and not missing else "FAIL"
    if state == "FAIL":
        ok = False
    print(f"{state} {path} -> {r.status_code} len={len(r.data)} missing={missing}")
    if path.endswith("csv"):
        print("   csv head:", r.data[:120])

# Redirect target check.
r = client.get("/admin/")
print("redirect /admin/ ->", r.headers.get("Location"))
print("ALL OK" if ok else "FAILURES FOUND")
