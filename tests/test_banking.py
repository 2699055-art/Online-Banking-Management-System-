import os
import re
import sqlite3
import tempfile
import threading
import unittest

from bankapp import create_app

PW = "Customer@123"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test", "SEED_DEMO": True,
            "DATABASE": os.path.join(self.tmp.name, "t.db"),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def client(self):
        c = self.app.test_client()
        html = c.get("/login").get_data(as_text=True)
        c.csrf = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
        return c

    def call(self, c, method, url, json=None, csrf=True):
        headers = {"X-CSRF-Token": c.csrf} if csrf else {}
        return getattr(c, method)(url, json=json, headers=headers)

    def login(self, email, password=PW):
        c = self.client()
        r = self.call(c, "post", "/api/auth/login", {"email": email, "password": password})
        if r.status_code == 200:
            c.csrf = r.get_json()["csrf"]
        return c, r

    def db(self):
        conn = sqlite3.connect(self.app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        return conn


class AuthTests(Base):
    def test_register_then_pending_account(self):
        c = self.client()
        r = self.call(c, "post", "/api/auth/register", {
            "full_name": "Neha Rao", "email": "neha@example.com", "phone": "9876543210",
            "password": "Secret123", "pin": "4321"})
        self.assertEqual(r.status_code, 201)
        c, r = self.login("neha@example.com", "Secret123")
        self.assertEqual(r.status_code, 200)
        me = self.call(c, "get", "/api/auth/me").get_json()
        self.assertEqual(me["account_status"], "pending")

    def test_register_validation(self):
        c = self.client()
        bad = {"full_name": "X1", "email": "nope", "phone": "12", "password": "short", "pin": "12"}
        r = self.call(c, "post", "/api/auth/register", bad)
        self.assertEqual(r.status_code, 400)
        r = self.call(c, "post", "/api/auth/register", {
            "full_name": "Neha Rao", "email": "aarav@bluepass.test", "phone": "9876543210",
            "password": "Secret123", "pin": "4321"})
        self.assertEqual(r.status_code, 409)

    def test_passwords_are_hashed(self):
        row = self.db().execute("SELECT password_hash, pin_hash FROM users WHERE email='aarav@bluepass.test'").fetchone()
        self.assertNotIn("Customer@123", row["password_hash"])
        self.assertNotIn("1234", row["pin_hash"])

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            _, r = self.login("aarav@bluepass.test", "wrong-pass1")
            self.assertEqual(r.status_code, 401)
        _, r = self.login("aarav@bluepass.test", PW)  # correct password, but locked
        self.assertEqual(r.status_code, 429)

    def test_unknown_user_same_error(self):
        _, r = self.login("ghost@example.com", "whatever1")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()["error"], "Incorrect email or password.")

    def test_csrf_required(self):
        c = self.client()
        r = self.call(c, "post", "/api/auth/login", {"email": "aarav@bluepass.test", "password": PW}, csrf=False)
        self.assertEqual(r.status_code, 403)

    def test_logout_ends_session(self):
        c, _ = self.login("aarav@bluepass.test")
        self.call(c, "post", "/api/auth/logout")
        self.assertEqual(self.call(c, "get", "/api/account").status_code, 401)

    def test_deactivated_user_blocked(self):
        admin, _ = self.login("admin@bluepass.test", "Admin@123")
        uid = self.db().execute("SELECT id FROM users WHERE email='meera@bluepass.test'").fetchone()["id"]
        self.assertEqual(self.call(admin, "post", f"/api/admin/users/{uid}/status", {"active": False}).status_code, 200)
        _, r = self.login("meera@bluepass.test")
        self.assertEqual(r.status_code, 403)

    def test_security_headers(self):
        r = self.client().get("/login")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")


class AuthorizationTests(Base):
    def test_role_boundaries(self):
        cust, _ = self.login("aarav@bluepass.test")
        for url in ("/api/admin/users", "/api/admin/stats", "/api/staff/pending"):
            self.assertEqual(self.call(cust, "get", url).status_code, 403, url)
        staff, _ = self.login("staff@bluepass.test", "Staff@123")
        self.assertEqual(self.call(staff, "get", "/api/admin/logs").status_code, 403)
        self.assertEqual(self.call(staff, "get", "/api/account").status_code, 403)
        self.assertEqual(self.call(staff, "get", "/api/staff/pending").status_code, 200)

    def test_anonymous_blocked(self):
        c = self.client()
        self.assertEqual(self.call(c, "get", "/api/account").status_code, 401)
        self.assertEqual(c.get("/dashboard").status_code, 302)

    def test_pages_redirect_by_role(self):
        staff, _ = self.login("staff@bluepass.test", "Staff@123")
        self.assertEqual(staff.get("/dashboard").headers["Location"], "/staff")
        self.assertEqual(staff.get("/admin").headers["Location"], "/staff")

    def test_customer_sees_only_own_transactions(self):
        meera, _ = self.login("meera@bluepass.test")
        items = self.call(meera, "get", "/api/transactions?per_page=100").get_json()["items"]
        acct = self.call(meera, "get", "/api/account").get_json()["account_number"]
        with self.db() as db:
            owner = {r["id"] for r in db.execute(
                "SELECT t.id FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE a.account_number=?", (acct,))}
        self.assertEqual({i["id"] for i in items}, owner)


class MoneyTests(Base):
    def balance(self, c):
        return self.call(c, "get", "/api/account").get_json()["balance"]

    def send(self, c, to="500000000002", amount="100", pin="1234", note=""):
        return self.call(c, "post", "/api/transfer", {"to_account": to, "amount": amount, "pin": pin, "note": note})

    def test_transfer_moves_money_and_logs_both_sides(self):
        a, _ = self.login("aarav@bluepass.test")
        m, _ = self.login("meera@bluepass.test")
        a0, m0 = self.balance(a), self.balance(m)
        r = self.send(a, amount="250.50", note="Lunch")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.balance(a), a0 - 25050)
        self.assertEqual(self.balance(m), m0 + 25050)
        top_a = self.call(a, "get", "/api/transactions?per_page=1").get_json()["items"][0]
        top_m = self.call(m, "get", "/api/transactions?per_page=1").get_json()["items"][0]
        self.assertEqual((top_a["type"], top_m["type"], top_a["ref"]), ("debit", "credit", top_m["ref"]))
        unread = self.call(m, "get", "/api/notifications").get_json()["unread"]
        self.assertGreaterEqual(unread, 1)

    def test_wrong_pin_and_pin_lockout(self):
        a, _ = self.login("aarav@bluepass.test")
        b0 = self.balance(a)
        for _ in range(2):
            self.assertEqual(self.send(a, pin="0000").status_code, 400)
        self.assertEqual(self.send(a, pin="0000").status_code, 400)  # third failure locks
        self.assertEqual(self.send(a, pin="1234").status_code, 429)
        self.assertEqual(self.balance(a), b0)

    def test_rules(self):
        a, _ = self.login("aarav@bluepass.test")
        self.assertEqual(self.send(a, amount="-5").status_code, 400)
        self.assertEqual(self.send(a, amount="0").status_code, 400)
        self.assertEqual(self.send(a, amount="1.234").status_code, 400)
        self.assertEqual(self.send(a, amount="1e3").status_code, 400)
        self.assertEqual(self.send(a, to="123").status_code, 400)
        self.assertEqual(self.send(a, to="599999999999").status_code, 404)
        own = self.call(a, "get", "/api/account").get_json()["account_number"]
        self.assertEqual(self.send(a, to=own).status_code, 400)
        self.assertEqual(self.send(a, amount="100001").status_code, 400)       # per-transfer cap
        self.assertEqual(self.send(a, amount="99999999").status_code, 400)

    def test_insufficient_funds_changes_nothing(self):
        m, _ = self.login("meera@bluepass.test")
        b0 = self.balance(m)
        r = self.send(m, to="500000000001", amount=str(b0 // 100 + 1))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.balance(m), b0)

    def test_daily_limit(self):
        with self.db() as db:
            db.execute("UPDATE accounts SET balance = 900000000 WHERE account_number='500000000001'")
        a, _ = self.login("aarav@bluepass.test")
        self.assertEqual(self.send(a, amount="100000").status_code, 201)
        self.assertEqual(self.send(a, amount="100000").status_code, 201)
        r = self.send(a, amount="1")
        self.assertEqual(r.status_code, 400)
        self.assertIn("daily limit", r.get_json()["error"])

    def test_pending_account_cannot_send_or_receive(self):
        c = self.client()
        self.call(c, "post", "/api/auth/register", {"full_name": "Neha Rao", "email": "neha@example.com",
                  "phone": "9876543210", "password": "Secret123", "pin": "4321"})
        n, _ = self.login("neha@example.com", "Secret123")
        self.assertEqual(self.send(n, pin="4321").status_code, 403)
        number = self.call(n, "get", "/api/account").get_json()["account_number"]
        a, _ = self.login("aarav@bluepass.test")
        self.assertEqual(self.send(a, to=number).status_code, 404)

    def test_concurrent_transfers_never_overdraw(self):
        with self.db() as db:
            db.execute("UPDATE accounts SET balance = 1000000 WHERE account_number='500000000001'")  # Rs 10,000
        results = []

        def worker():
            with self.app.test_client() as c:
                html = c.get("/login").get_data(as_text=True)
                tok = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
                r = c.post("/api/auth/login", json={"email": "aarav@bluepass.test", "password": PW}, headers={"X-CSRF-Token": tok})
                tok = r.get_json()["csrf"]
                r = c.post("/api/transfer", json={"to_account": "500000000002", "amount": "4000", "pin": "1234"},
                           headers={"X-CSRF-Token": tok})
                results.append(r.status_code)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results).count(201), 2)     # only two Rs 4,000 transfers fit in Rs 10,000
        with self.db() as db:
            bal = db.execute("SELECT balance FROM accounts WHERE account_number='500000000001'").fetchone()[0]
            total = db.execute("SELECT SUM(balance) FROM accounts").fetchone()[0]
        self.assertEqual(bal, 200000)
        self.assertGreaterEqual(total, 0)

    def test_ledger_balances_reconcile(self):
        with self.db() as db:
            for acct in db.execute("SELECT id, balance FROM accounts WHERE status='active'").fetchall():
                last = db.execute("SELECT balance_after FROM transactions WHERE account_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                                  (acct["id"],)).fetchone()
                self.assertEqual(last["balance_after"], acct["balance"])


class StaffAdminTests(Base):
    def test_verification_flow(self):
        c = self.client()
        self.call(c, "post", "/api/auth/register", {"full_name": "Neha Rao", "email": "neha@example.com",
                  "phone": "9876543210", "password": "Secret123", "pin": "4321"})
        staff, _ = self.login("staff@bluepass.test", "Staff@123")
        pending = self.call(staff, "get", "/api/staff/pending").get_json()["items"]
        self.assertTrue(any(p["email"] == "neha@example.com" for p in pending))
        target = next(p for p in pending if p["email"] == "neha@example.com")
        self.assertEqual(self.call(staff, "post", f"/api/staff/accounts/{target['id']}/review", {"action": "reject"}).status_code, 400)  # reason required
        self.assertEqual(self.call(staff, "post", f"/api/staff/accounts/{target['id']}/review", {"action": "approve"}).status_code, 200)
        n, _ = self.login("neha@example.com", "Secret123")
        self.assertEqual(self.call(n, "get", "/api/account").get_json()["status"], "active")
        self.assertEqual(self.call(staff, "post", f"/api/staff/accounts/{target['id']}/review", {"action": "approve"}).status_code, 409)

    def test_teller_deposit_and_withdrawal(self):
        staff, _ = self.login("staff@bluepass.test", "Staff@123")
        r = self.call(staff, "post", "/api/staff/teller", {"account_number": "500000000002", "kind": "deposit", "amount": "500"})
        self.assertEqual(r.status_code, 201)
        r = self.call(staff, "post", "/api/staff/teller", {"account_number": "500000000002", "kind": "withdrawal", "amount": "99999999"})
        self.assertEqual(r.status_code, 400)
        r = self.call(staff, "post", "/api/staff/teller", {"account_number": "500000000003", "kind": "deposit", "amount": "5"})
        self.assertEqual(r.status_code, 409)  # pending account

    def test_freeze_blocks_transfers(self):
        staff, _ = self.login("staff@bluepass.test", "Staff@123")
        acct_id = self.db().execute("SELECT id FROM accounts WHERE account_number='500000000001'").fetchone()["id"]
        self.call(staff, "post", f"/api/staff/accounts/{acct_id}/review", {"action": "freeze", "reason": "Suspicious activity"})
        a, _ = self.login("aarav@bluepass.test")
        r = self.call(a, "post", "/api/transfer", {"to_account": "500000000002", "amount": "10", "pin": "1234"})
        self.assertEqual(r.status_code, 403)

    def test_admin_creates_staff_and_logs_actions(self):
        admin, _ = self.login("admin@bluepass.test", "Admin@123")
        r = self.call(admin, "post", "/api/admin/users", {"full_name": "New Staff", "email": "new@bluepass.test",
                      "password": "Passw0rd!", "role": "staff"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.call(admin, "post", "/api/admin/users", {"full_name": "Bad Role", "email": "b@bluepass.test",
                         "password": "Passw0rd!", "role": "customer"}).status_code, 400)
        logs = self.call(admin, "get", "/api/admin/logs?q=user_created").get_json()["items"]
        self.assertEqual(logs[0]["action"], "user_created")
        me = self.db().execute("SELECT id FROM users WHERE email='admin@bluepass.test'").fetchone()["id"]
        self.assertEqual(self.call(admin, "post", f"/api/admin/users/{me}/status", {"active": False}).status_code, 400)

    def test_logs_never_contain_secrets(self):
        a, _ = self.login("aarav@bluepass.test")
        self.call(a, "post", "/api/transfer", {"to_account": "500000000002", "amount": "10", "pin": "1234"})
        self.login("aarav@bluepass.test", "Wrong-pass9")
        with self.db() as db:
            blob = " ".join(f"{r['detail']} {r['action']}" for r in db.execute("SELECT * FROM activity_logs"))
        self.assertNotIn("Customer@123", blob)
        self.assertNotIn("Wrong-pass9", blob)


class ReportTests(Base):
    def test_summary_and_filters(self):
        a, _ = self.login("aarav@bluepass.test")
        s = self.call(a, "get", "/api/reports/summary?tz=330").get_json()
        self.assertGreater(s["count"], 5)
        self.assertEqual(s["credits"], sum(m["credits"] for m in s["months"]))
        only_debits = self.call(a, "get", "/api/transactions?type=debit&per_page=100").get_json()["items"]
        self.assertTrue(all(t["type"] == "debit" for t in only_debits))
        self.assertEqual(self.call(a, "get", "/api/transactions?from=bad-date").status_code, 400)

    def test_search_escapes_wildcards(self):
        a, _ = self.login("aarav@bluepass.test")
        r = self.call(a, "get", "/api/transactions?q=%25").get_json()
        self.assertEqual(r["total"], 0)

    def test_csv_export_neutralises_formulas(self):
        with self.db() as db:
            db.execute("UPDATE transactions SET note='=HYPERLINK(\"http://evil\")' WHERE ref='SEED00015'")
        a, _ = self.login("aarav@bluepass.test")
        r = self.call(a, "get", "/api/reports/statement.csv")
        self.assertEqual(r.mimetype, "text/csv")
        body = r.get_data(as_text=True)
        self.assertIn("'=HYPERLINK", body)
        self.assertNotIn(",=HYPERLINK", body)


class PageTests(Base):
    def test_every_page_renders_for_its_role(self):
        for email, pw, urls in [
            ("aarav@bluepass.test", PW, ["/dashboard", "/activity", "/transfer", "/reports", "/notifications", "/profile"]),
            ("staff@bluepass.test", "Staff@123", ["/staff", "/profile"]),
            ("admin@bluepass.test", "Admin@123", ["/admin", "/staff", "/profile"]),
        ]:
            c, _ = self.login(email, pw)
            for u in urls:
                self.assertEqual(c.get(u).status_code, 200, f"{email} {u}")
        anon = self.client()
        for u in ("/login", "/register"):
            self.assertEqual(anon.get(u).status_code, 200)
        self.assertEqual(anon.get("/nope").status_code, 404)
        self.assertEqual(anon.get("/api/nope").get_json()["error"], "We couldn't find that page.")

    def test_templates_have_no_inline_script_or_style(self):
        root = os.path.join(os.path.dirname(__file__), "..", "bankapp", "templates")
        for f in os.listdir(root):
            with open(os.path.join(root, f)) as fh:
                src = fh.read()
            self.assertNotRegex(src, r"<script(?![^>]*\bsrc=)", f)
            self.assertNotIn('style="', src, f)


if __name__ == "__main__":
    unittest.main()
