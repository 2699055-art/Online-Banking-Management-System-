"""REST API: customer features (account, transactions, transfers, beneficiaries,
reports, notifications)."""
import csv
import io

from flask import Blueprint, Response, current_app, g, jsonify, request

from . import validators as v
from .api_auth import body
from .db import get_db
from .errors import ApiError
from .security import api_roles, log_activity, verify_pin
from .services import notify, transfer_funds

bp = Blueprint("customer", __name__, url_prefix="/api")


def my_account(db):
    acct = db.execute("SELECT * FROM accounts WHERE user_id=?", (g.user["id"],)).fetchone()
    if not acct:
        raise ApiError("No account found.", 404)
    return acct


def tx_dict(r):
    return {k: r[k] for k in ("id", "ref", "type", "category", "amount", "balance_after",
                              "counterparty_account", "counterparty_name", "note", "created_at")}


# ---------------------------------------------------------------------- account
@bp.get("/account")
@api_roles("customer")
def account():
    db, cfg = get_db(), current_app.config
    a = my_account(db)
    used = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE account_id=? AND category='transfer' "
        "AND type='debit' AND created_at >= datetime('now','-1 day')", (a["id"],)).fetchone()[0]
    return jsonify(
        account_number=a["account_number"], balance=a["balance"], status=a["status"],
        status_reason=a["status_reason"], full_name=g.user["full_name"],
        daily_limit=cfg["DAILY_TRANSFER_LIMIT"], daily_used=used,
        single_limit=cfg["SINGLE_TRANSFER_LIMIT"],
    )


# ----------------------------------------------------------------- transactions
def tx_filters(args, account_id):
    where, params = ["account_id = ?"], [account_id]
    tz = f"{v.tz_offset(args.get('tz')):+d} minutes"
    if args.get("type") in ("credit", "debit"):
        where.append("type = ?"); params.append(args["type"])
    if args.get("category") in ("transfer", "deposit", "withdrawal"):
        where.append("category = ?"); params.append(args["category"])
    if args.get("q"):
        like = v.like_pattern(args["q"])
        where.append("(counterparty_name LIKE ? ESCAPE '\\' OR note LIKE ? ESCAPE '\\' "
                     "OR ref LIKE ? ESCAPE '\\')")
        params += [like, like, like]
    start, end = v.date_str(args.get("from"), "from"), v.date_str(args.get("to"), "to")
    if start:
        where.append("date(created_at, ?) >= ?"); params += [tz, start]
    if end:
        where.append("date(created_at, ?) <= ?"); params += [tz, end]
    return " AND ".join(where), params, tz


@bp.get("/transactions")
@api_roles("customer")
def transactions():
    db = get_db()
    a = my_account(db)
    where, params, _ = tx_filters(request.args, a["id"])
    per_page = max(1, min(100, request.args.get("per_page", 20, type=int)))
    page = max(1, request.args.get("page", 1, type=int))
    total = db.execute(f"SELECT COUNT(*) FROM transactions WHERE {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM transactions WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page]).fetchall()
    return jsonify(items=[tx_dict(r) for r in rows], total=total, page=page,
                   pages=max(1, -(-total // per_page)))


@bp.post("/transfer")
@api_roles("customer")
def transfer():
    d, db = body(), get_db()
    to = v.account_number(d.get("to_account"), "to_account")
    amount = v.amount_paise(d.get("amount"))
    note = v.text(d.get("note"), "note", "Note", max_len=60, required=False)
    verify_pin(g.user, d.get("pin"))
    return jsonify(transfer_funds(db, g.user, to, amount, note)), 201


@bp.get("/lookup/<number>")
@api_roles("customer")
def lookup(number):
    number = v.account_number(number)
    row = get_db().execute(
        "SELECT u.full_name FROM accounts a JOIN users u ON u.id=a.user_id "
        "WHERE a.account_number=? AND a.status='active'", (number,)).fetchone()
    if not row:
        raise ApiError("We couldn't find an active account with that number.", 404, "to_account")
    return jsonify(name=row["full_name"])


# ----------------------------------------------------------------- beneficiaries
@bp.get("/beneficiaries")
@api_roles("customer")
def beneficiaries():
    rows = get_db().execute(
        "SELECT id, account_number, name, nickname FROM beneficiaries WHERE user_id=? "
        "ORDER BY name", (g.user["id"],)).fetchall()
    return jsonify(items=[dict(r) for r in rows])


@bp.post("/beneficiaries")
@api_roles("customer")
def add_beneficiary():
    d, db = body(), get_db()
    number = v.account_number(d.get("account_number"))
    nick = v.text(d.get("nickname"), "nickname", "Nickname", max_len=30, required=False)
    if number == my_account(db)["account_number"]:
        raise ApiError("That's your own account number.", 400, "account_number")
    row = db.execute(
        "SELECT u.full_name FROM accounts a JOIN users u ON u.id=a.user_id "
        "WHERE a.account_number=? AND a.status='active'", (number,)).fetchone()
    if not row:
        raise ApiError("We couldn't find an active account with that number.", 404, "account_number")
    count = db.execute("SELECT COUNT(*) FROM beneficiaries WHERE user_id=?", (g.user["id"],)).fetchone()[0]
    if count >= current_app.config["MAX_BENEFICIARIES"]:
        raise ApiError("You've reached the limit of saved recipients. Remove one first.", 400)
    if db.execute("SELECT 1 FROM beneficiaries WHERE user_id=? AND account_number=?",
                  (g.user["id"], number)).fetchone():
        raise ApiError("This recipient is already saved.", 409, "account_number")
    db.execute("INSERT INTO beneficiaries (user_id, account_number, name, nickname) VALUES (?,?,?,?)",
               (g.user["id"], number, row["full_name"], nick or None))
    log_activity("beneficiary_added", number)
    return jsonify(ok=True), 201


@bp.delete("/beneficiaries/<int:bid>")
@api_roles("customer")
def delete_beneficiary(bid):
    cur = get_db().execute("DELETE FROM beneficiaries WHERE id=? AND user_id=?", (bid, g.user["id"]))
    if cur.rowcount == 0:
        raise ApiError("Recipient not found.", 404)
    log_activity("beneficiary_removed", str(bid))
    return jsonify(ok=True)


# ---------------------------------------------------------------------- reports
@bp.get("/reports/summary")
@api_roles("customer")
def report_summary():
    db = get_db()
    a = my_account(db)
    where, params, tz = tx_filters(request.args, a["id"])
    totals = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN type='credit' THEN amount END),0) AS credits, "
        "COALESCE(SUM(CASE WHEN type='debit' THEN amount END),0) AS debits, COUNT(*) AS n "
        f"FROM transactions WHERE {where}", params).fetchone()
    months = db.execute(
        "SELECT strftime('%Y-%m', created_at, ?) AS month, "
        "COALESCE(SUM(CASE WHEN type='credit' THEN amount END),0) AS credits, "
        "COALESCE(SUM(CASE WHEN type='debit' THEN amount END),0) AS debits "
        f"FROM transactions WHERE {where} GROUP BY month ORDER BY month", [tz] + params).fetchall()
    cats = db.execute(
        "SELECT category, type, SUM(amount) AS total, COUNT(*) AS n "
        f"FROM transactions WHERE {where} GROUP BY category, type ORDER BY total DESC", params).fetchall()
    top = db.execute(
        "SELECT counterparty_name AS name, SUM(amount) AS total, COUNT(*) AS n "
        f"FROM transactions WHERE {where} AND type='debit' AND category='transfer' "
        "GROUP BY counterparty_account ORDER BY total DESC LIMIT 5", params).fetchall()
    return jsonify(
        credits=totals["credits"], debits=totals["debits"], count=totals["n"],
        months=[dict(r) for r in months], categories=[dict(r) for r in cats],
        top_recipients=[dict(r) for r in top])


def _csv_safe(cell):
    """Stop spreadsheet formula injection when a statement is opened in Excel."""
    s = str(cell if cell is not None else "")
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@bp.get("/reports/statement.csv")
@api_roles("customer")
def statement_csv():
    db = get_db()
    a = my_account(db)
    where, params, tz = tx_filters(request.args, a["id"])
    rows = db.execute(
        f"SELECT datetime(created_at, ?) AS local_time, * FROM transactions WHERE {where} "
        "ORDER BY created_at, id", [tz] + params).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Reference", "Description", "Note", "Debit", "Credit", "Balance"])
    for r in rows:
        desc = {"deposit": "Cash deposit", "withdrawal": "Cash withdrawal"}.get(
            r["category"], ("From " if r["type"] == "credit" else "To ") + (r["counterparty_name"] or ""))
        amt = f"{r['amount'] / 100:.2f}"
        w.writerow([r["local_time"], r["ref"], _csv_safe(desc), _csv_safe(r["note"]),
                    amt if r["type"] == "debit" else "", amt if r["type"] == "credit" else "",
                    f"{r['balance_after'] / 100:.2f}"])
    log_activity("statement_export", f"{len(rows)} rows")
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=statement-{a['account_number']}.csv"})


# ---------------------------------------------------------------- notifications
@bp.get("/notifications")
@api_roles()
def notifications():
    db = get_db()
    rows = db.execute("SELECT id,title,body,is_read,created_at FROM notifications "
                      "WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT 50",
                      (g.user["id"],)).fetchall()
    unread = db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                        (g.user["id"],)).fetchone()[0]
    return jsonify(items=[dict(r) for r in rows], unread=unread)


@bp.post("/notifications/read")
@api_roles()
def mark_read():
    get_db().execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    return jsonify(ok=True)
