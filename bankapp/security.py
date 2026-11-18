"""Security & additional services: sessions, CSRF, hashing, lockouts, role checks,
activity logging and HTTP security headers."""
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, g, jsonify, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .errors import ApiError

_DUMMY_HASH = generate_password_hash("not-a-real-password-1")
HOME_FOR_ROLE = {"customer": "/dashboard", "staff": "/staff", "admin": "/admin"}


# ---------------------------------------------------------------- time helpers
def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S") if value else None


# --------------------------------------------------------------- activity logs
def log_activity(action, detail="", user_id=None, actor=None):
    """Append-only audit trail. Never pass passwords, PINs or tokens in `detail`."""
    user = getattr(g, "user", None)
    if user_id is None and user is not None:
        user_id = user["id"]
    if actor is None and user is not None:
        actor = user["email"]
    get_db().execute(
        "INSERT INTO activity_logs (user_id, actor, action, detail, ip) VALUES (?,?,?,?,?)",
        (user_id, actor, action, (detail or "")[:500], request.remote_addr or "-"),
    )


# ------------------------------------------------------------ passwords & PINs
def hash_secret(value):
    return generate_password_hash(value)


def verify_password(user, password):
    """Constant-ish time: hashes a dummy value when the user doesn't exist."""
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return False
    return check_password_hash(user["password_hash"], password)


def _minutes_left(until):
    return max(1, int((until - utcnow()).total_seconds() // 60) + 1)


def verify_pin(user, pin):
    """Check the transaction PIN with lockout after repeated failures."""
    db = get_db()
    cfg = current_app.config
    locked = parse_ts(user["pin_locked_until"])
    if locked and locked > utcnow():
        raise ApiError(
            f"PIN locked after too many attempts. Try again in {_minutes_left(locked)} min.",
            429, "pin",
        )
    if not user["pin_hash"] or not check_password_hash(user["pin_hash"], pin or ""):
        failed = user["pin_failed"] + 1
        if failed >= cfg["MAX_PIN_ATTEMPTS"]:
            until = utcnow() + timedelta(minutes=cfg["LOCKOUT_MINUTES"])
            db.execute("UPDATE users SET pin_failed=0, pin_locked_until=? WHERE id=?",
                       (ts(until), user["id"]))
            log_activity("pin_locked", "Too many wrong PIN attempts")
        else:
            db.execute("UPDATE users SET pin_failed=? WHERE id=?", (failed, user["id"]))
            log_activity("pin_failed", f"Wrong PIN ({failed}/{cfg['MAX_PIN_ATTEMPTS']})")
        raise ApiError("Incorrect PIN.", 400, "pin")
    if user["pin_failed"]:
        db.execute("UPDATE users SET pin_failed=0 WHERE id=?", (user["id"],))


# -------------------------------------------------------------------- sessions
def start_session(user):
    session.clear()  # prevents session fixation
    session["uid"] = user["id"]
    session["csrf"] = secrets.token_urlsafe(32)
    session.permanent = True


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


# --------------------------------------------------------------- role guards
def api_roles(*roles):
    """Protect a JSON endpoint. No roles = any signed-in user."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if g.user is None:
                raise ApiError("Please sign in to continue.", 401)
            if roles and g.user["role"] not in roles:
                log_activity("access_denied", f"{request.method} {request.path}")
                raise ApiError("You don't have access to this.", 403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def page_roles(*roles):
    """Protect an HTML page; redirect instead of returning JSON."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if g.user is None:
                return redirect("/login")
            if roles and g.user["role"] not in roles:
                return redirect(HOME_FOR_ROLE[g.user["role"]])
            return fn(*args, **kwargs)
        return wrapper
    return deco


# ---------------------------------------------------------------------- hooks
def init_security(app):
    @app.before_request
    def _load_user_and_check_csrf():
        g.user = None
        uid = session.get("uid")
        if uid:
            row = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if row and row["is_active"]:
                g.user = row
            else:
                session.clear()
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/"):
            sent = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(sent, expected):
                raise ApiError("Your session expired. Refresh the page and try again.", 403)

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        if request.path.startswith("/api/") or resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _inject():
        return {"csrf_token": csrf_token(), "idle_minutes": app.config["IDLE_MINUTES"],
                "user": g.get("user")}
