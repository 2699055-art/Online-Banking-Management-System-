# BluePass: Online & Mobile Banking System

A working banking web app built from the architecture diagram: **HTML + CSS** frontend,
**Python Flask** REST backend, **SQLite** database, plus the security and notification services.
The UI is mobile-first (bottom tab bar on phones, side rail on desktop) and styled like a bank passbook.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                                          # http://127.0.0.1:5000
```

On first start the database is created in `instance/bluepass.db` and filled with demo data.

| Role | Email | Password | PIN |
|---|---|---|---|
| Customer | aarav@bluepass.test | Customer@123 | 1234 |
| Customer | meera@bluepass.test | Customer@123 | 1234 |
| Customer (unverified) | rohan@bluepass.test | Customer@123 | 1234 |
| Bank staff | staff@bluepass.test | Staff@123 | n/a |
| Admin | admin@bluepass.test | Admin@123 | n/a |

Run tests: `python -m unittest discover -s tests -v`

## How the diagram maps to the code

| Diagram box | Where it lives |
|---|---|
| **Users**: Customer, Admin, Bank Staff | `users.role`; role guards in `bankapp/security.py` |
| **Frontend**: Login/Register, Dashboard, Transactions, Reports | `bankapp/templates/*.html`, `static/css/app.css`, `static/js/app.js` |
| **HTTP (REST API)** | `bankapp/api_*.py` (JSON under `/api/...`) |
| Authentication & Authorization | `api_auth.py`, `security.py` (`api_roles`, `page_roles`) |
| Account Management | `services.register_customer`, `services.review_account`, `api_staff.py` |
| Transaction Processing | `services.transfer_funds`, `services.teller_operation` |
| Notifications | `notifications` table, `services.notify`, bell + `/notifications` page |
| Admin Operations | `api_admin.py`, `/admin` page |
| **Database**: Users, Accounts, Transactions, Beneficiaries, Logs | `bankapp/db.py` (schema) |
| Data Security | Hashed passwords & PINs, secure cookies, CSP and security headers |
| Input Validation | `bankapp/validators.py` |
| Session Management | 15-minute idle timeout, session reset on login, CSRF token |
| Activity Logs | `activity_logs` table, admin "Activity log" tab |

## What each role can do

- **Customer**: register, sign in, see balance and recent activity, search and filter transactions, send money (confirmed with a transaction PIN), save recipients, view reports (monthly chart, by type, top recipients), download a CSV statement, print, read notifications, change password and PIN.
- **Bank staff** (`/staff`): verify or reject new accounts, post cash deposits and withdrawals at the counter, search customers, freeze or unfreeze accounts.
- **Admin** (`/admin`): system overview, create staff or admin users, activate or deactivate logins, unlock locked users, browse the audit log. Admins can also use the service desk.

New customers start as **pending**; money can't move until staff verify the account.

## REST API (all JSON; non-GET calls need the `X-CSRF-Token` header)

| Method & path | Who | Purpose |
|---|---|---|
| POST `/api/auth/register`, `/login`, `/logout` | public / any | Sign up, sign in, sign out |
| GET `/api/auth/me` | any | Current user |
| POST `/api/auth/change-password`, `/change-pin` | any / customer | Credentials |
| GET `/api/account` | customer | Balance, status, limits |
| GET `/api/transactions?type&q&from&to&page` | customer | Ledger with filters |
| POST `/api/transfer` | customer | `{to_account, amount, note, pin}` |
| GET `/api/lookup/<account>` | customer | Recipient name check |
| GET/POST/DELETE `/api/beneficiaries` | customer | Saved recipients |
| GET `/api/reports/summary`, `/reports/statement.csv` | customer | Reports and export |
| GET `/api/notifications`, POST `/notifications/read` | any | In-app notifications |
| GET `/api/staff/pending`, `/customers`; POST `/accounts/<id>/review`, `/teller` | staff, admin | Service desk |
| GET `/api/admin/stats`, `/users`, `/logs`; POST `/users`, `/users/<id>/status`, `/users/<id>/unlock` | admin | Administration |

## Security built in

- Passwords and PINs stored as salted hashes (scrypt via Werkzeug); never logged.
- Login locks for 15 minutes after 5 failures; the PIN locks after 3. Unknown emails get the same error and timing as wrong passwords.
- Sessions: HttpOnly, SameSite=Lax cookies, new session on login, 15-minute idle timeout (server and browser), CSRF token on every state-changing call.
- Strict Content-Security-Policy (no inline scripts or styles), `X-Frame-Options: DENY`, `nosniff`, no-store caching for pages and API.
- All SQL is parameterised; LIKE searches escape wildcards; the frontend builds the DOM with `textContent`, never `innerHTML`.
- Money is stored as integer paise. Transfers run inside `BEGIN IMMEDIATE`, so two simultaneous requests can't overdraw an account (covered by a test). Per-transfer and rolling 24-hour limits apply.
- CSV exports neutralise spreadsheet formula injection.
- Every sensitive action is written to the activity log.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BANK_SECRET_KEY` | generated in `instance/secret.key` | Session signing key |
| `BANK_DB` | `instance/bluepass.db` | SQLite file |
| `BANK_SEED` | `1` | Set to `0` to skip demo data |
| `BANK_HTTPS` | unset | Set to `1` behind HTTPS to mark cookies Secure |
| `HOST`, `PORT`, `FLASK_DEBUG` | `127.0.0.1`, `5000`, off | Dev server |

Limits and timeouts are in `bankapp/config.py`. Create a real admin with
`BANK_SEED=0 flask --app run create-admin you@example.com "Your Name"`.

## Before using this for anything real

This is a learning and prototype project, not a licensed banking platform. For production you would add:
a production WSGI server behind HTTPS, a server-grade database (PostgreSQL) instead of SQLite, IP-based rate limiting,
email/SMS verification and one-time passwords, real email/SMS delivery for notifications, backups, and a security review.
**Turn off demo seeding (`BANK_SEED=0`) so the demo passwords above don't exist.**

## Project layout

```
run.py                  entry point
bankapp/
  __init__.py           app factory, error handlers
  config.py db.py       settings, SQLite schema and transactions
  security.py           sessions, CSRF, lockouts, role guards, audit log
  validators.py         input validation
  services.py           account + transaction business logic
  api_auth.py api_customer.py api_staff.py api_admin.py   REST API
  pages.py              HTML routes
  seed.py               demo data
  templates/ static/    frontend
tests/test_banking.py   31 tests
```
