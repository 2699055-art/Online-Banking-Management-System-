"""Input validation (the 'Input Validation' box in the diagram)."""
import re
from datetime import datetime

from .errors import ApiError

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?\d{10,13}$")
ACCOUNT_RE = re.compile(r"^\d{12}$")
AMOUNT_RE = re.compile(r"^\d{1,9}(\.\d{1,2})?$")
PIN_RE = re.compile(r"^\d{4,6}$")


def text(value, field, label, *, min_len=1, max_len=100, required=True):
    if value is None or value == "":
        if required:
            raise ApiError(f"{label} is required.", 400, field)
        return ""
    if not isinstance(value, str):
        raise ApiError(f"{label} must be text.", 400, field)
    value = " ".join(value.split())  # trim and collapse whitespace
    if any(ord(c) < 32 for c in value):
        raise ApiError(f"{label} contains invalid characters.", 400, field)
    if len(value) < min_len or len(value) > max_len:
        raise ApiError(f"{label} must be {min_len}-{max_len} characters.", 400, field)
    return value


def full_name(value, field="full_name"):
    v = text(value, field, "Full name", min_len=2, max_len=80)
    if not all(c.isalpha() or c in " .'-" for c in v):
        raise ApiError("Full name can only contain letters, spaces, . ' and -", 400, field)
    return v


def email(value, field="email"):
    v = text(value, field, "Email", max_len=254).lower()
    if not EMAIL_RE.match(v):
        raise ApiError("Enter a valid email address.", 400, field)
    return v


def phone(value, field="phone"):
    v = text(value, field, "Phone number", max_len=16).replace(" ", "")
    if not PHONE_RE.match(v):
        raise ApiError("Enter a phone number with 10-13 digits.", 400, field)
    return v


def password(value, field="password"):
    if not isinstance(value, str) or not (8 <= len(value) <= 128):
        raise ApiError("Password must be 8-128 characters.", 400, field)
    if not (re.search(r"[A-Za-z]", value) and re.search(r"\d", value)):
        raise ApiError("Password must include at least one letter and one number.", 400, field)
    return value


def pin(value, field="pin"):
    if not isinstance(value, str) or not PIN_RE.match(value):
        raise ApiError("PIN must be 4-6 digits.", 400, field)
    return value


def account_number(value, field="account_number"):
    v = value.strip() if isinstance(value, str) else ""
    if not ACCOUNT_RE.match(v):
        raise ApiError("Account number must be 12 digits.", 400, field)
    return v


def amount_paise(value, field="amount"):
    """'1234.50' -> 123450. Rejects negatives, exponents and >2 decimals."""
    s = str(value).strip() if value is not None else ""
    if not AMOUNT_RE.match(s):
        raise ApiError("Enter an amount like 250 or 250.50.", 400, field)
    rupees, _, frac = s.partition(".")
    paise = int(rupees) * 100 + int((frac + "00")[:2])
    if paise <= 0:
        raise ApiError("Amount must be greater than zero.", 400, field)
    return paise


def date_str(value, field):
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ApiError("Use the date format YYYY-MM-DD.", 400, field)
    return value


def tz_offset(value):
    try:
        n = int(value or 0)
    except ValueError:
        n = 0
    return max(-840, min(840, n))


def like_pattern(value):
    """Escape LIKE wildcards in user search text (use with ESCAPE '\\')."""
    v = (value or "").strip()[:60]
    return "%" + v.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
