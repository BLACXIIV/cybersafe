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

    @app.before_request
    def redirect_probe_host_to_real_host():
        from urllib.parse import urlsplit
        real_host = urlsplit(app.config["PORTAL_BASE_URL"]).netloc  # e.g. "cybersafe.local:8000"
        if request.method != "GET":
            return  # never redirect POST — risks the browser dropping the body/converting to GET
        if request.host == real_host:
            return  # already on the real host (normal access, or already redirected once)
        # Any other Host header reaching this app is, by construction, a device
        # that isn't authorized yet and got here via the Pi's NAT redirect using
        # some OS captive-portal probe's fake host (msftconnecttest.com, etc.).
        # Send a real redirect so the browser's address bar updates to the
        # actual app address — how commercial captive portals behave.
        return redirect(app.config["PORTAL_BASE_URL"] + request.full_path.rstrip("?"), code=302)

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

    app.run(debug=True, host="0.0.0.0", port=5000)
