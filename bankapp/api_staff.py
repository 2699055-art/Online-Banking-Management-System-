"""REST API: bank staff (verify & support). Admins can use these too."""
from flask import Blueprint, g, jsonify, request

from . import validators as v
from .api_auth import body
from .db import get_db
from .security import api_roles
from .services import review_account, teller_operation
from . import validators

bp = Blueprint("staff", __name__, url_prefix="/api/staff")


@bp.get("/pending")
@api_roles("staff", "admin")
def pending():
    rows = get_db().execute(
        "SELECT a.id, a.account_number, a.created_at, u.full_name, u.email, u.phone "
        "FROM accounts a JOIN users u ON u.id=a.user_id WHERE a.status='pending' "
        "ORDER BY a.created_at").fetchall()
    return jsonify(items=[dict(r) for r in rows])


@bp.post("/accounts/<int:account_id>/review")
@api_roles("staff", "admin")
def review(account_id):
    d = body()
    reason = v.text(d.get("reason"), "reason", "Reason", max_len=120, required=False)
    status = review_account(get_db(), g.user, account_id, d.get("action"), reason)
    return jsonify(ok=True, status=status)


@bp.get("/customers")
@api_roles("staff", "admin")
def customers():
    q = request.args.get("q", "")
    like = validators.like_pattern(q)
    rows = get_db().execute(
        "SELECT a.id, a.account_number, a.status, a.balance, u.full_name, u.email, u.phone "
        "FROM accounts a JOIN users u ON u.id=a.user_id "
        "WHERE u.full_name LIKE ? ESCAPE '\\' OR u.email LIKE ? ESCAPE '\\' "
        "OR a.account_number LIKE ? ESCAPE '\\' ORDER BY u.full_name LIMIT 25",
        (like, like, like)).fetchall()
    return jsonify(items=[dict(r) for r in rows])


@bp.post("/teller")
@api_roles("staff", "admin")
def teller():
    d = body()
    number = v.account_number(d.get("account_number"))
    amount = v.amount_paise(d.get("amount"))
    note = v.text(d.get("note"), "note", "Note", max_len=60, required=False)
    return jsonify(teller_operation(get_db(), g.user, number, d.get("kind"), amount, note)), 201
