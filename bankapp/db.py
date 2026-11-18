"""Database layer (SQLite)."""
import contextlib
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    phone           TEXT,
    password_hash   TEXT NOT NULL,
    pin_hash        TEXT,
    role            TEXT NOT NULL CHECK (role IN ('customer','staff','admin')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    failed_logins   INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    pin_failed      INTEGER NOT NULL DEFAULT 0,
    pin_locked_until TEXT,
    last_login      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL UNIQUE REFERENCES users(id),
    account_number  TEXT NOT NULL UNIQUE,
    balance         INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','active','rejected','frozen')),
    status_reason   TEXT,
    reviewed_by     INTEGER REFERENCES users(id),
    reviewed_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id                   INTEGER PRIMARY KEY,
    ref                  TEXT NOT NULL,
    account_id           INTEGER NOT NULL REFERENCES accounts(id),
    type                 TEXT NOT NULL CHECK (type IN ('credit','debit')),
    category             TEXT NOT NULL CHECK (category IN ('transfer','deposit','withdrawal')),
    amount               INTEGER NOT NULL CHECK (amount > 0),
    balance_after        INTEGER NOT NULL,
    counterparty_account TEXT,
    counterparty_name    TEXT,
    note                 TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_account_time ON transactions(account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tx_ref ON transactions(ref);

CREATE TABLE IF NOT EXISTS beneficiaries (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    account_number  TEXT NOT NULL,
    name            TEXT NOT NULL,
    nickname        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, account_number)
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS activity_logs (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER,
    actor       TEXT,
    action      TEXT NOT NULL,
    detail      TEXT,
    ip          TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_logs_time ON activity_logs(created_at);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(
            current_app.config["DATABASE"], timeout=10, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    get_db().executescript(SCHEMA)


@contextlib.contextmanager
def transaction(db):
    """Atomic write block. BEGIN IMMEDIATE takes the write lock up front so two
    simultaneous transfers cannot both read the same balance."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")
