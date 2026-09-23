from flask import Flask, g, redirect, render_template, request, session, url_for
from markupsafe import Markup

from config import Config
from database.db import register_app, init_db, ensure_admin_data, get_db
from extensions import limiter


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if app.debug:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    register_app(app)

    app.config.setdefault("RATELIMIT_ENABLED", not app.config.get("TESTING", False))
    limiter.init_app(app)

    # IPs that just arrived via a captive-portal probe (fake Host header) and
    # have not yet made their first request on the real host. The session
    # cookie lives under the real host — a fake-host request never carries it
    # and a Set-Cookie can't reach across hosts — so the reconnect logout has
    # to happen on that first real-host request instead.
    fresh_connect_ips = set()

    @app.before_request
    def redirect_probe_host_to_real_host():
        from urllib.parse import urlsplit
        real_host = urlsplit(app.config["PORTAL_BASE_URL"]).netloc  # e.g. "cybersafe.local:8000"
        if request.method != "GET":
            return  # never redirect POST — risks the browser dropping the body/converting to GET
        if request.host != real_host:
            # A fresh WiFi connection always starts with a fake-host
            # captive-portal probe. Clear whatever session rode in under the
            # fake host, and mark the device so its first request back on the
            # real host — where the session cookie actually lives — starts a
            # new session too. This forces a new login after every reconnect,
            # for everyone uniformly — admin accounts included, that's
            # intentional, not a bug.
            session.clear()
            if request.remote_addr:
                fresh_connect_ips.add(request.remote_addr)
            return redirect(app.config["PORTAL_BASE_URL"] + request.full_path.rstrip("?"), code=302)
        if request.remote_addr in fresh_connect_ips:
            # First request on the real host after a fresh connect: clear the
            # session (the cookie is actually present in this request, unlike
            # on the probe request above), then continue — the request is
            # handled as logged-out, which for protected pages means a
            # redirect to /login.
            fresh_connect_ips.discard(request.remote_addr)
            session.clear()

    @app.errorhandler(429)
    def too_many_requests(error):
        return render_template("429.html"), 429

    @app.errorhandler(404)
    def captive_portal_redirect(error):
        if request.method == "GET":
            return redirect(url_for("main.landing"))
        return error, 404

    @app.context_processor
    def inject_school_settings():
        settings = get_db().execute("SELECT * FROM school_settings WHERE id = 1").fetchone()
        return {"school_settings": settings}

    @app.context_processor
    def inject_internet_status():
        status = {
            "active_voucher_flag": False,
            "block_tests_when_active": app.config.get("BLOCK_TESTS_WHEN_ACTIVE", False),
        }
        if hasattr(g, "user") and g.user:
            import levels as _levels
            db = get_db()
            status["active_voucher_flag"] = _levels._has_active_voucher(db, g.user["id"])
        return status

    @app.context_processor
    def inject_rank_up():
        return {"rank_up": session.pop("rank_up", None)}

    from lucide import lucide_icon

    @app.template_global()
    def lucide(name, **kwargs):
        try:
            if 'class' in kwargs:
                kwargs['cls'] = kwargs.pop('class')
            if 'size' in kwargs:
                size = kwargs.pop('size')
                kwargs.setdefault('width', size)
                kwargs.setdefault('height', size)
            return Markup(lucide_icon(name, **kwargs))
        except Exception:
            return Markup('')

    import auth
    import main
    import levels
    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(levels.bp)
    import admin
    app.register_blueprint(admin.bp)
    ensure_admin_data(app)

    return app


app = create_app()

if __name__ == "__main__":
    import os
    if not os.path.exists(app.config["DATABASE_PATH"]):
        init_db(app)
        ensure_admin_data(app)
        print("Database initialized at", app.config["DATABASE_PATH"])

    # Dev-only default so the portal host check doesn't bounce local
    # requests to the deployed Pi; production runs gunicorn, which never
    # reaches this block and keeps the cybersafe.local default.
    app.config["PORTAL_BASE_URL"] = os.environ.get(
        "CYBERSAFE_PORTAL_BASE_URL", "http://localhost:5000"
    )
    app.run(debug=True, host="0.0.0.0", port=5000)
