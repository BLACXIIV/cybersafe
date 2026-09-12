import re
from app import create_app

app = create_app()
app.config.update({"TESTING": True, "RATELIMIT_ENABLED": False})
c = app.test_client()
c.post("/login", data={"identifier": "123456789012", "step": "1"})
c.post("/login", data={"step": "2", "password": "admin"})

r = c.get("/admin/report?period=week&badge=Bronze").data.decode()
# pair each chip label with its href
chips = re.findall(r'href="([^"]+)"[^>]*>\s*(Today|This week|This month|This year|All time)\s*<', r)
for href, label in chips:
    print(f"{label:12} -> {href}")
