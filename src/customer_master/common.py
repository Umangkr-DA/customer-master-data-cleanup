"""Shared paths, logging, reference data, and the six data-quality rules.

Every pipeline script imports from here so that a rule or path is defined exactly once.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
from pathlib import Path

# ----------------------------------------------------------------------------- paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "ground_truth" / "answer_key.csv"  # evaluation ONLY
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"
SQL_DIR = PROJECT_ROOT / "sql"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
REPORT_DIR = PROJECT_ROOT / "reports"
DB_PATH = PROCESSED_DIR / "customer_mdm.db"
REPORT_PATH = REPORT_DIR / "customer_data_quality_report.xlsx"

SEED = 42
SOURCE_FILES = ["crm", "web", "sales"]
FIELDS = ["full_name", "phone", "email", "city", "pincode", "signup_date"]

# ----------------------------------------------------------------------------- reference data
REF_DATE = dt.date(2026, 10, 8)  # fixed "as of" date so runs are reproducible
MIN_DATE = dt.date(2015, 1, 1)

# canonical city -> first 3 digits of the PIN codes used in that city
CITY_PIN_PREFIX = {
    "Bhopal": "462",
    "Indore": "452",
    "Jabalpur": "482",
    "Jaipur": "302",
    "Mumbai": "400",
    "Pune": "411",
    "Nagpur": "440",
    "Delhi": "110",
    "Lucknow": "226",
    "Ahmedabad": "380",
    "Bengaluru": "560",
    "Chennai": "600",
    "Hyderabad": "500",
    "Kolkata": "700",
}
CITIES = list(CITY_PIN_PREFIX)

# rule id -> (field, definition)
RULES = {
    "R1": ("Name", "Full name present, 2+ alphabetic words, Title Case, no stray spaces"),
    "R2": ("Phone", "Exactly 10 digits, Indian mobile (starts 6-9), not one repeated digit"),
    "R3": ("Email", "Present, lowercase, valid user@domain.tld format"),
    "R4": ("City", "Present and one of the 14 reference cities (canonical spelling)"),
    "R5": ("Pincode", "6 digits, first digit 1-8, prefix agrees with the city when city is known"),
    "R6": ("Signup date", "ISO yyyy-mm-dd, real calendar date, between 2015-01-01 and run date"),
}

NAME_RE = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)+$")
PHONE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}$")
PIN_RE = re.compile(r"^[1-8]\d{5}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ----------------------------------------------------------------------------- validation rules
def _text(value) -> str | None:
    return None if value is None or (isinstance(value, float) and value != value) else str(value)


def r1_name(value) -> bool:
    v = _text(value)
    return bool(v and NAME_RE.match(v))


def r2_phone(value) -> bool:
    v = _text(value)
    return bool(v and PHONE_RE.match(v) and len(set(v)) > 1)


def r3_email(value) -> bool:
    v = _text(value)
    return bool(v and EMAIL_RE.match(v))


def r4_city(value) -> bool:
    return _text(value) in CITY_PIN_PREFIX


def r5_pincode(pincode, city) -> bool:
    pin, city = _text(pincode), _text(city)
    if not (pin and PIN_RE.match(pin)):
        return False
    if city in CITY_PIN_PREFIX:
        return pin.startswith(CITY_PIN_PREFIX[city])
    return True


def r6_date(value) -> bool:
    v = _text(value)
    if not (v and ISO_RE.match(v)):
        return False
    try:
        d = dt.date.fromisoformat(v)
    except ValueError:
        return False
    return MIN_DATE <= d <= REF_DATE


def validate_row(name, phone, email, city, pincode, signup_date) -> dict[str, int]:
    """Apply R1..R6 to one record. Returns {rule_id: 1 if passed else 0}."""
    return {
        "R1": int(r1_name(name)),
        "R2": int(r2_phone(phone)),
        "R3": int(r3_email(email)),
        "R4": int(r4_city(city)),
        "R5": int(r5_pincode(pincode, city)),
        "R6": int(r6_date(signup_date)),
    }


# ----------------------------------------------------------------------------- name matching (used inside SQL)
def name_match(name_a: str | None, name_b: str | None) -> int:
    """Name guard used by the matcher.

    1  = same person  (same surname AND same first name, or one first name is an initial of the other)
    0  = conflict     (different surname or different first name)
    -1 = cannot tell  (a name is missing)
    """
    if not name_a or not name_b:
        return -1
    tokens_a, tokens_b = name_a.lower().split(), name_b.lower().split()
    if tokens_a[-1] != tokens_b[-1]:
        return 0
    first_a, first_b = tokens_a[0], tokens_b[0]
    if first_a == first_b:
        return 1
    if (len(first_a) == 1 and first_b.startswith(first_a)) or (len(first_b) == 1 and first_a.startswith(first_b)):
        return 1
    return 0


# ----------------------------------------------------------------------------- logging / database helpers
def get_logger(name: str) -> logging.Logger:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    return logging.getLogger(name)


def connect() -> sqlite3.Connection:
    """Open the pipeline database (created on first use) with the name_match() SQL function registered."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.create_function("name_match", 2, name_match)
    return con


def run_sql(con: sqlite3.Connection, filename: str) -> None:
    """Execute a script from the sql/ folder."""
    path = SQL_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing SQL script: {path}")
    con.executescript(path.read_text(encoding="utf-8"))
