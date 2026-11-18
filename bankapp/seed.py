"""Demo data so the app is useful the first time it runs (demo credentials only!)."""
from datetime import timedelta

from .security import hash_secret, ts, utcnow

DEMO_USERS = [
    # name, email, password, pin, role, account number, status
    ("Priya Admin", "admin@bluepass.test", "Admin@123", None, "admin", None, None),
    ("Sanjay Staff", "staff@bluepass.test", "Staff@123", None, "staff", None, None),
    ("Aarav Mehta", "aarav@bluepass.test", "Customer@123", "1234", "customer", "500000000001", "active"),
    ("Meera Nair", "meera@bluepass.test", "Customer@123", "1234", "customer", "500000000002", "active"),
    ("Rohan Iyer", "rohan@bluepass.test", "Customer@123", "1234", "customer", "500000000003", "pending"),
]

# (days_ago, owner, kind, amount_rupees, other_owner, note)
EVENTS = [
    (150, "aarav", "deposit", 150000, None, "Opening deposit"),
    (140, "meera", "deposit", 60000, None, "Opening deposit"),
    (120, "aarav", "deposit", 85000, None, "Salary"),
    (110, "meera", "deposit", 70000, None, "Salary"),
    (100, "aarav", "transfer", 12000, "meera", "Rent share"),
    (90, "aarav", "deposit", 85000, None, "Salary"),
    (80, "aarav", "withdrawal", 10000, None, "Cash for travel"),
    (70, "aarav", "transfer", 4500, "meera", "Dinner split"),
    (60, "aarav", "deposit", 85000, None, "Salary"),
    (50, "meera", "deposit", 70000, None, "Salary"),
    (45, "meera", "transfer", 3000, "aarav", "Refund"),
    (35, "aarav", "transfer", 8000, "meera", "Weekend trip"),
    (30, "aarav", "deposit", 85000, None, "Salary"),
    (20, "aarav", "withdrawal", 5000, None, "ATM cash"),
    (5, "aarav", "transfer", 2500, "meera", "Groceries"),
    (2, "aarav", "deposit", 85000, None, "Salary"),
]


def seed_demo(db):
    ids, accounts = {}, {}
    for name, email, pw, pin, role, number, status in DEMO_USERS:
        cur = db.execute(
            "INSERT INTO users (full_name,email,password_hash,pin_hash,role,phone) VALUES (?,?,?,?,?,?)",
            (name, email, hash_secret(pw), hash_secret(pin) if pin else None, role,
             "9800000000" if role == "customer" else None))
        key = email.split("@")[0].split(".")[0]
        ids[key] = cur.lastrowid
        if number:
            a = db.execute("INSERT INTO accounts (user_id, account_number, status) VALUES (?,?,?)",
                           (cur.lastrowid, number, status))
            accounts[key] = {"id": a.lastrowid, "number": number, "name": name, "balance": 0}

    now, n = utcnow(), 0
    for days, owner, kind, rupees, other, note in sorted(EVENTS, key=lambda e: -e[0]):
        n += 1
        when = ts(now - timedelta(days=days, hours=n % 5))
        amt, ref = rupees * 100, f"SEED{n:05d}"
        src = accounts[owner]
        if kind == "deposit":
            src["balance"] += amt
            rows = [(src, "credit", "deposit", "Branch counter", None)]
        elif kind == "withdrawal":
            src["balance"] -= amt
            rows = [(src, "debit", "withdrawal", "Branch counter", None)]
        else:
            dst = accounts[other]
            src["balance"] -= amt
            dst["balance"] += amt
            rows = [(src, "debit", "transfer", dst["name"], dst["number"]),
                    (dst, "credit", "transfer", src["name"], src["number"])]
        for acct, typ, cat, cp_name, cp_num in rows:
            db.execute(
                "INSERT INTO transactions (ref,account_id,type,category,amount,balance_after,"
                "counterparty_account,counterparty_name,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ref, acct["id"], typ, cat, amt, acct["balance"], cp_num, cp_name, note, when))
    for acct in accounts.values():
        db.execute("UPDATE accounts SET balance=? WHERE id=?", (acct["balance"], acct["id"]))
    db.execute("INSERT INTO notifications (user_id,title,body) VALUES (?,?,?)",
               (ids["aarav"], "Welcome to BluePass", "Your account is verified and ready to use."))
    db.execute("INSERT INTO notifications (user_id,title,body) VALUES (?,?,?)",
               (ids["rohan"], "Welcome to BluePass",
                "Your account is waiting for verification by our bank staff."))
    db.execute("INSERT INTO beneficiaries (user_id,account_number,name,nickname) VALUES (?,?,?,?)",
               (ids["aarav"], "500000000002", "Meera Nair", "Meera"))
