"""Central configuration. Override with environment variables."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def load_secret_key() -> str:
    """Use BANK_SECRET_KEY if set, otherwise create and reuse instance/secret.key."""
    env = os.environ.get("BANK_SECRET_KEY")
    if env:
        return env
    INSTANCE_DIR.mkdir(exist_ok=True)
    key_file = INSTANCE_DIR / "secret.key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    return key_file.read_text().strip()


class Config:
    DATABASE = os.environ.get("BANK_DB", str(INSTANCE_DIR / "bluepass.db"))
    SEED_DEMO = os.environ.get("BANK_SEED", "1") == "1"

    # Session management
    IDLE_MINUTES = 15                       # idle timeout
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("BANK_HTTPS") == "1"

    # Brute-force protection
    MAX_LOGIN_ATTEMPTS = 5
    MAX_PIN_ATTEMPTS = 3
    LOCKOUT_MINUTES = 15

    # Money rules (all amounts are stored in paise: 1 rupee = 100 paise)
    MIN_TRANSFER = 100                      # Rs 1
    SINGLE_TRANSFER_LIMIT = 100_000_00      # Rs 1,00,000 per transfer
    DAILY_TRANSFER_LIMIT = 200_000_00       # Rs 2,00,000 rolling 24 hours
    MAX_BENEFICIARIES = 25
