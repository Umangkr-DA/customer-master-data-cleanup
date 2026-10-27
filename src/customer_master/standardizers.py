"""Field standardizers. Contract: return a canonical value, or None when the input cannot be
repaired deterministically. Unfixable values are left blank - never guessed."""

import re
import difflib
import datetime as dt
from customer_master.common import CITY_PIN_PREFIX, REF_DATE, MIN_DATE, PHONE_RE, EMAIL_RE, PIN_RE

PLACEHOLDERS = {"", "n/a", "na", "unknown", "customer", "tbd", "null", "none", "nan"}
TITLES = {"mr", "mrs", "ms", "dr", "shri", "smt"}


def _blank(v):
    return v is None or str(v).strip().lower() in PLACEHOLDERS


# ---------- name ----------
def std_name(v):
    if _blank(v):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    if "," in s:  # "Sharma, Rahul" -> "Rahul Sharma"
        last, _, first = s.partition(",")
        s = f"{first.strip()} {last.strip()}"
    words = [w for w in s.replace(".", " ").split() if w.lower() not in TITLES]
    if len(words) < 2 or not all(w.isalpha() for w in words):
        return None
    return " ".join(w.capitalize() for w in words)


# ---------- phone ----------
def std_phone(v):
    if _blank(v):
        return None
    d = re.sub(r"\D", "", str(v))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if PHONE_RE.match(d) and len(set(d)) > 1:
        return d
    return None


# ---------- email ----------
DOMAIN_FIXES = {
    "gmail.con": "gmail.com",
    "gmial.com": "gmail.com",
    "yahho.com": "yahoo.com",
    "outlok.com": "outlook.com",
    "hotmial.com": "hotmail.com",
    "rediffmail.co": "rediffmail.com",
}


def std_email(v):
    if _blank(v):
        return None
    s = str(v).strip().lower()
    if "@" in s:
        user, _, dom = s.rpartition("@")
        s = f"{user}@{DOMAIN_FIXES.get(dom, dom)}"
    if EMAIL_RE.match(s) and not s.endswith("@test.test"):
        return s
    return None


# ---------- city ----------
_CANON = {c.lower(): c for c in CITY_PIN_PREFIX}
CITY_ALIASES = {
    "bangalore": "Bengaluru",
    "bengalooru": "Bengaluru",
    "bombay": "Mumbai",
    "calcutta": "Kolkata",
    "madras": "Chennai",
    "new delhi": "Delhi",
    "bpl": "Bhopal",
    "bhopal mp": "Bhopal",
    "bhopal m p": "Bhopal",
}


def std_city(v):
    if _blank(v):
        return None
    s = re.sub(r"[^a-z ]", " ", str(v).lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s in _CANON:
        return _CANON[s]
    if s in CITY_ALIASES:
        return CITY_ALIASES[s]
    close = difflib.get_close_matches(s, list(_CANON), n=2, cutoff=0.82)
    if len(close) == 1 or (
        len(close) == 2
        and difflib.SequenceMatcher(None, s, close[0]).ratio() - difflib.SequenceMatcher(None, s, close[1]).ratio()
        > 0.1
    ):
        return _CANON[close[0]]  # single unambiguous near-match = typo
    return None


# ---------- pincode ----------
def std_pincode(v):
    if _blank(v):
        return None
    s = str(v).strip()
    s = re.sub(r"\.0$", "", s)  # Excel float artefact "462001.0"
    s = re.sub(r"[\s-]", "", s)
    return s if PIN_RE.match(s) else None


# ---------- date ----------
DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%b-%Y", "%d %B %Y"]  # dd/mm = Indian convention


def std_date(v):
    if _blank(v):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    for fmt in DATE_FORMATS:
        try:
            d = dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        return d.isoformat() if MIN_DATE <= d <= REF_DATE else None
    return None


def standardize_record(r):
    name = std_name(r["full_name"])
    city = std_city(r["city"])
    pin = std_pincode(r["pincode"])
    # pincode/city conflict -> we cannot know which is right, so blank the pincode only
    # when the city is known and prefixes disagree (conservative, not a guess)
    if city and pin and not pin.startswith(CITY_PIN_PREFIX[city]):
        pin = None
    return dict(
        full_name=name,
        phone=std_phone(r["phone"]),
        email=std_email(r["email"]),
        city=city,
        pincode=pin,
        signup_date=std_date(r["signup_date"]),
    )
