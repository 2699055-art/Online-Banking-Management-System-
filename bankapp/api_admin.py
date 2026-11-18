"""REST API: admin operations (manage the system)."""
from flask import Blueprint, g, jsonify, request

from . import validators as v
from .api_auth import body
from .db import get_db
from .errors import ApiError
from .security import api_roles, hash_secret, log_activity

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/stats")
@api_roles("admin")
def stats():
    db = get_db()
    one = lambda sql: db.execute(sql).fetchone()[0]
    return jsonify(
        customers=one("SELECT COUNT(*) FROM users WHERE role='customer'"),
        staff=one("SELECT COUNT(*) FROM users WHERE role='staff'"),
        admins=one("SELECT COUNT(*) FROM users WHERE role='admin'"),
        pending=one("SELECT COUNT(*) FROM accounts WHERE status='pending'"),
        frozen=one("SELECT COUNT(*) FROM accounts WHERE status='frozen'"),
        deposits=one("SELECT COALESCE(SUM(balance),0) FROM accounts"),
        tx_24h=one("SELECT COUNT(DISTINCT ref) FROM transactions WHERE created_at >= datetime('now','-1 day')"),
        failed_logins_24h=one("SELECT COUNT(*) FROM activity_logs WHERE action='login_failed' "
                              "AND created_at >= datetime('now','-1 day')"),
    )


@bp.get("/users")
@api_roles("admin")
def users():
    where, params = ["1=1"], []
    if request.args.get("role") in ("customer", "staff", "admin"):
        where.append("u.role=?"); params.append(request.args["role"])
    if request.args.get("q"):
        like = v.like_pattern(request.args["q"])
        where.append("(u.full_name LIKE ? ESCAPE '\\' OR u.email LIKE ? ESCAPE '\\')")
        params += [like, like]
    rows = get_db().execute(
        "SELECT u.id, u.full_name, u.email, u.role, u.is_active, u.last_login, u.locked_until, "
        "u.pin_locked_until, a.account_number, a.status AS account_status "
        "FROM users u LEFT JOIN accounts a ON a.user_id=u.id "
        f"WHERE {' AND '.join(where)} ORDER BY u.role, u.full_name LIMIT 100", params).fetchall()
    return jsonify(items=[dict(r) for r in rows])


@bp.post("/users")
@api_roles("admin")
def create_user():
    d, db = body(), get_db()
    name, email = v.full_name(d.get("full_name")), v.email(d.get("email"))
    password = v.password(d.get("password"))
    role = d.get("role")
    if role not in ("staff", "admin"):
        raise ApiError("Role must be staff or admin.", 400, "role")
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError("A user with this email already exists.", 409, "email")
    db.execute("INSERT INTO users (full_name,email,password_hash,role) VALUES (?,?,?,?)",
               (name, email, hash_secret(password), role))
    log_activity("user_created", f"{role} {email}")
    return jsonify(ok=True), 201


@bp.post("/users/<int:uid>/status")
@api_roles("admin")
def set_status(uid):
    d, db = body(), get_db()
    active = 1 if d.get("active") else 0
    if uid == g.user["id"]:
        raise ApiError("You can't deactivate your own login.", 400)
    cur = db.execute("UPDATE users SET is_active=? WHERE id=?", (active, uid))
    if cur.rowcount == 0:
        raise ApiError("User not found.", 404)
    log_activity("user_activated" if active else "user_deactivated", f"user {uid}")
    return jsonify(ok=True)


@bp.post("/users/<int:uid>/unlock")
@api_roles("admin")
def unlock(uid):
    cur = get_db().execute(
        "UPDATE users SET failed_logins=0, locked_until=NULL, pin_failed=0, pin_locked_until=NULL "
        "WHERE id=?", (uid,))
    if cur.rowcount == 0:
        raise ApiError("User not found.", 404)
    log_activity("user_unlocked", f"user {uid}")
    return jsonify(ok=True)


@bp.get("/logs")
@api_roles("admin")
def logs():
    db = get_db()
    where, params = ["1=1"], []
    if request.args.get("q"):
        like = v.like_pattern(request.args["q"])
        where.append("(action LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\' OR actor LIKE ? ESCAPE '\\')")
        params += [like, like, like]
    page = max(1, request.args.get("page", 1, type=int))
    per = 25
    total = db.execute(f"SELECT COUNT(*) FROM activity_logs WHERE {' AND '.join(where)}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT id, actor, action, detail, ip, created_at FROM activity_logs WHERE {' AND '.join(where)} "
        "ORDER BY id DESC LIMIT ? OFFSET ?", params + [per, (page - 1) * per]).fetchall()
    return jsonify(items=[dict(r) for r in rows], page=page, pages=max(1, -(-total // per)), total=total)
