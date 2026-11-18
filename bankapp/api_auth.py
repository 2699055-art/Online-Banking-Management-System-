"""REST API: authentication & authorization."""
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash

from . import validators as v
from .db import get_db
from .errors import ApiError
from .security import (HOME_FOR_ROLE, _minutes_left, api_roles, hash_secret, log_activity,
                       parse_ts, start_session, ts, utcnow, verify_password)
from .services import register_customer

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Send a JSON object.", 400)
    return data


def public_user(user, db):
    acct = db.execute("SELECT account_number, status FROM accounts WHERE user_id=?",
                      (user["id"],)).fetchone()
    return {
        "id": user["id"], "full_name": user["full_name"], "email": user["email"],
        "phone": user["phone"], "role": user["role"], "last_login": user["last_login"],
        "account_number": acct["account_number"] if acct else None,
        "account_status": acct["status"] if acct else None,
    }


@bp.post("/register")
def register():
    d = body()
    name = v.full_name(d.get("full_name"))
    email = v.email(d.get("email"))
    phone = v.phone(d.get("phone"))
    password = v.password(d.get("password"))
    pin = v.pin(d.get("pin"))
    db = get_db()
    user_id, _ = register_customer(db, name, email, phone, password, pin)
    log_activity("register", "New customer registered", user_id=user_id, actor=email)
    return jsonify(ok=True), 201


@bp.post("/login")
def login():
    d = body()
    email = v.text(d.get("email"), "email", "Email", max_len=254).lower()
    password = d.get("password") if isinstance(d.get("password"), str) else ""
    cfg, db = current_app.config, get_db()
    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

    locked = parse_ts(user["locked_until"]) if user else None
    if locked and locked > utcnow():
        raise ApiError(f"Too many failed attempts. Try again in {_minutes_left(locked)} min.", 429)

    if not verify_password(user, password):
        if user:
            failed = user["failed_logins"] + 1
            if failed >= cfg["MAX_LOGIN_ATTEMPTS"]:
                until = utcnow() + timedelta(minutes=cfg["LOCKOUT_MINUTES"])
                db.execute("UPDATE users SET failed_logins=0, locked_until=? WHERE id=?",
                           (ts(until), user["id"]))
                log_activity("login_locked", "Account locked after repeated failures",
                             user_id=user["id"], actor=email)
            else:
                db.execute("UPDATE users SET failed_logins=? WHERE id=?", (failed, user["id"]))
        log_activity("login_failed", "Incorrect credentials", user_id=user["id"] if user else None,
                     actor=email)
        raise ApiError("Incorrect email or password.", 401)

    if not user["is_active"]:
        log_activity("login_blocked", "Deactivated user", user_id=user["id"], actor=email)
        raise ApiError("This login has been deactivated. Please contact the bank.", 403)

    db.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login=? WHERE id=?",
               (ts(utcnow()), user["id"]))
    start_session(user)
    g.user = user
    log_activity("login", "Signed in")
    return jsonify(ok=True, csrf=session["csrf"], redirect=HOME_FOR_ROLE[user["role"]])


@bp.post("/logout")
@api_roles()
def logout():
    log_activity("logout", "Signed out")
    session.clear()
    return jsonify(ok=True)


@bp.get("/me")
@api_roles()
def me():
    return jsonify(public_user(g.user, get_db()))


@bp.post("/change-password")
@api_roles()
def change_password():
    d = body()
    if not check_password_hash(g.user["password_hash"], d.get("current_password") or ""):
        raise ApiError("Your current password is incorrect.", 400, "current_password")
    new = v.password(d.get("new_password"), "new_password")
    get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (hash_secret(new), g.user["id"]))
    log_activity("password_changed", "Password updated")
    return jsonify(ok=True)


@bp.post("/change-pin")
@api_roles("customer")
def change_pin():
    d = body()
    if not check_password_hash(g.user["password_hash"], d.get("current_password") or ""):
        raise ApiError("Your password is incorrect.", 400, "current_password")
    new = v.pin(d.get("new_pin"), "new_pin")
    get_db().execute("UPDATE users SET pin_hash=?, pin_failed=0, pin_locked_until=NULL WHERE id=?",
                     (hash_secret(new), g.user["id"]))
    log_activity("pin_changed", "Transaction PIN updated")
    return jsonify(ok=True)
