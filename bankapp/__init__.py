"""BluePass online / mobile banking system (Flask + SQLite)."""
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, jsonify, render_template, request

from .config import Config, load_secret_key
from .db import close_db, get_db, init_db
from .errors import ApiError
from .security import init_security


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_secret_key()
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.permanent_session_lifetime = timedelta(minutes=app.config["IDLE_MINUTES"])
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)
    init_security(app)

    from . import api_admin, api_auth, api_customer, api_staff, pages
    for module in (pages, api_auth, api_customer, api_staff, api_admin):
        app.register_blueprint(module.bp)

    # ----- error handling: JSON for /api, friendly page otherwise
    @app.errorhandler(ApiError)
    def _api_error(e):
        return jsonify(error=e.message, field=e.field), e.status

    def _http_error(status, message):
        if request.path.startswith("/api/"):
            return jsonify(error=message), status
        return render_template("error.html", page="error", title="Error", status=status,
                               message=message, nav=[]), status

    @app.errorhandler(404)
    def _404(_):
        return _http_error(404, "We couldn't find that page.")

    @app.errorhandler(405)
    def _405(_):
        return _http_error(405, "That action isn't allowed here.")

    @app.errorhandler(413)
    def _413(_):
        return _http_error(413, "That request is too large.")

    @app.errorhandler(500)
    def _500(_):
        return _http_error(500, "Something went wrong on our side. Please try again.")

    # ----- database bootstrap + demo data
    with app.app_context():
        init_db()
        if app.config["SEED_DEMO"] and not get_db().execute("SELECT 1 FROM users").fetchone():
            from .seed import seed_demo
            seed_demo(get_db())

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("name")
    @click.password_option()
    def create_admin(email, name, password):
        """Create an administrator:  flask --app run create-admin EMAIL NAME"""
        from . import validators as v
        from .security import hash_secret
        db = get_db()
        db.execute("INSERT INTO users (full_name,email,password_hash,role) VALUES (?,?,?, 'admin')",
                   (v.full_name(name), v.email(email), hash_secret(v.password(password))))
        click.echo("Admin created.")

    return app
