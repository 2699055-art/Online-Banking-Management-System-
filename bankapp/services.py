"""Business logic: account management, transaction processing, notifications."""
import secrets

from flask import current_app

from .db import transaction
from .errors import ApiError
from .security import hash_secret, log_activity


def inr(paise):
    """Format paise as Indian-grouped rupees, e.g. 12345678 -> Rs 1,23,456.78"""
    rupees, p = divmod(int(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"\u20b9{s}.{p:02d}"


# ---------------------------------------------------------------- notifications
def notify(db, user_id, title, body):
    db.execute("INSERT INTO notifications (user_id, title, body) VALUES (?,?,?)",
               (user_id, title, body))


# --------------------------------------------------------------------- accounts
def _new_account_number(db):
    while True:
        number = "5" + "".join(secrets.choice("0123456789") for _ in range(11))
        if not db.execute("SELECT 1 FROM accounts WHERE account_number=?", (number,)).fetchone():
            return number


def register_customer(db, full_name, email, phone, password, pin):
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError("An account with this email already exists.", 409, "email")
    with transaction(db):
        cur = db.execute(
            "INSERT INTO users (full_name,email,phone,password_hash,pin_hash,role) "
            "VALUES (?,?,?,?,?,'customer')",
            (full_name, email, phone, hash_secret(password), hash_secret(pin)),
        )
        user_id = cur.lastrowid
        number = _new_account_number(db)
        db.execute("INSERT INTO accounts (user_id, account_number) VALUES (?,?)", (user_id, number))
        notify(db, user_id, "Welcome to BluePass",
               "Your account is waiting for verification by our bank staff. "
               "You'll be notified as soon as it's active.")
    return user_id, number


def review_account(db, staff, account_id, action, reason):
    acct = db.execute(
        "SELECT a.*, u.full_name FROM accounts a JOIN users u ON u.id=a.user_id WHERE a.id=?",
        (account_id,),
    ).fetchone()
    if not acct:
        raise ApiError("Account not found.", 404)
    transitions = {
        "approve": ("pending", "active"),
        "reject": ("pending", "rejected"),
        "freeze": ("active", "frozen"),
        "unfreeze": ("frozen", "active"),
    }
    if action not in transitions:
        raise ApiError("Unknown action.", 400, "action")
    required, new_status = transitions[action]
    if acct["status"] != required:
        raise ApiError(f"Only {required} accounts can be set to {action}.", 409)
    if action in ("reject", "freeze") and not reason:
        raise ApiError("Add a reason so the customer knows what to do next.", 400, "reason")

    messages = {
        "approve": ("Account verified", "Your account is active. You can now send money."),
        "reject": ("Verification unsuccessful", f"We couldn't verify your account. {reason}"),
        "freeze": ("Account frozen", f"Your account has been frozen. {reason}"),
        "unfreeze": ("Account active again", "Your account is active again."),
    }
    with transaction(db):
        db.execute(
            "UPDATE accounts SET status=?, status_reason=?, reviewed_by=?, "
            "reviewed_at=datetime('now') WHERE id=?",
            (new_status, reason or None, staff["id"], account_id),
        )
        notify(db, acct["user_id"], *messages[action])
    log_activity(f"account_{action}", f"account {acct['account_number']}")
    return new_status


# ------------------------------------------------------------------ transactions
def _lookup_active(db, number):
    return db.execute(
        "SELECT a.*, u.full_name, u.id AS owner_id FROM accounts a "
        "JOIN users u ON u.id=a.user_id WHERE a.account_number=?", (number,),
    ).fetchone()


def transfer_funds(db, sender, to_number, amount, note):
    cfg = current_app.config
    if amount < cfg["MIN_TRANSFER"]:
        raise ApiError(f"Minimum transfer is {inr(cfg['MIN_TRANSFER'])}.", 400, "amount")
    if amount > cfg["SINGLE_TRANSFER_LIMIT"]:
        raise ApiError(f"Maximum per transfer is {inr(cfg['SINGLE_TRANSFER_LIMIT'])}.", 400, "amount")

    ref = "TXN" + secrets.token_hex(5).upper()
    with transaction(db):  # write lock held: balances below can't change under us
        src = db.execute("SELECT * FROM accounts WHERE user_id=?", (sender["id"],)).fetchone()
        if not src or src["status"] != "active":
            raise ApiError("Your account isn't active, so you can't send money right now.", 403)
        dst = _lookup_active(db, to_number)
        if not dst or dst["status"] != "active":
            raise ApiError("We couldn't find an active account with that number.", 404, "to_account")
        if dst["id"] == src["id"]:
            raise ApiError("You can't send money to your own account.", 400, "to_account")

        used = db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE account_id=? "
            "AND category='transfer' AND type='debit' AND created_at >= datetime('now','-1 day')",
            (src["id"],),
        ).fetchone()[0]
        if used + amount > cfg["DAILY_TRANSFER_LIMIT"]:
            left = max(0, cfg["DAILY_TRANSFER_LIMIT"] - used)
            raise ApiError(f"This is over your daily limit. You can send {inr(left)} more today.",
                           400, "amount")
        if src["balance"] < amount:
            raise ApiError("Your balance is too low for this transfer.", 400, "amount")

        src_bal, dst_bal = src["balance"] - amount, dst["balance"] + amount
        db.execute("UPDATE accounts SET balance=? WHERE id=?", (src_bal, src["id"]))
        db.execute("UPDATE accounts SET balance=? WHERE id=?", (dst_bal, dst["id"]))
        db.execute(
            "INSERT INTO transactions (ref,account_id,type,category,amount,balance_after,"
            "counterparty_account,counterparty_name,note) VALUES (?,?,?,?,?,?,?,?,?)",
            (ref, src["id"], "debit", "transfer", amount, src_bal,
             dst["account_number"], dst["full_name"], note),
        )
        db.execute(
            "INSERT INTO transactions (ref,account_id,type,category,amount,balance_after,"
            "counterparty_account,counterparty_name,note) VALUES (?,?,?,?,?,?,?,?,?)",
            (ref, dst["id"], "credit", "transfer", amount, dst_bal,
             src["account_number"], sender["full_name"], note),
        )
        notify(db, sender["id"], "Money sent", f"{inr(amount)} sent to {dst['full_name']}. Ref {ref}.")
        notify(db, dst["owner_id"], "Money received",
               f"{inr(amount)} received from {sender['full_name']}. Ref {ref}.")
    log_activity("transfer", f"{ref} {inr(amount)} to {dst['account_number']}")
    return {"ref": ref, "amount": amount, "balance": src_bal, "to_name": dst["full_name"]}


def teller_operation(db, staff, number, kind, amount, note):
    """Cash deposit / withdrawal handled by bank staff at the counter."""
    if kind not in ("deposit", "withdrawal"):
        raise ApiError("Choose deposit or withdrawal.", 400, "kind")
    ref = "CSH" + secrets.token_hex(5).upper()
    with transaction(db):
        acct = _lookup_active(db, number)
        if not acct:
            raise ApiError("No account with that number.", 404, "account_number")
        if acct["status"] != "active":
            raise ApiError("This account isn't active.", 409, "account_number")
        if kind == "withdrawal" and acct["balance"] < amount:
            raise ApiError("The customer's balance is too low for this withdrawal.", 400, "amount")
        new_bal = acct["balance"] + amount if kind == "deposit" else acct["balance"] - amount
        db.execute("UPDATE accounts SET balance=? WHERE id=?", (new_bal, acct["id"]))
        db.execute(
            "INSERT INTO transactions (ref,account_id,type,category,amount,balance_after,"
            "counterparty_name,note) VALUES (?,?,?,?,?,?,?,?)",
            (ref, acct["id"], "credit" if kind == "deposit" else "debit", kind, amount,
             new_bal, "Branch counter", note),
        )
        verb = "deposited to" if kind == "deposit" else "withdrawn from"
        notify(db, acct["owner_id"], f"Cash {kind}", f"{inr(amount)} {verb} your account. Ref {ref}.")
    log_activity(f"teller_{kind}", f"{ref} {inr(amount)} account {number}")
    return {"ref": ref, "balance": new_bal, "name": acct["full_name"]}
