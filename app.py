from flask import Flask, g

from config import Config
from database.db import register_app, init_db, ensure_admin_data, get_db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    register_app(app)  # wires up db.close on teardown + `flask init-db` command

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
    # Auto-create the database on first run so `python app.py` just works.
    if not os.path.exists(app.config["DATABASE_PATH"]):
        init_db(app)
        ensure_admin_data(app)
        print("Database initialized at", app.config["DATABASE_PATH"])

    app.run(debug=True, host="0.0.0.0", port=5000)
