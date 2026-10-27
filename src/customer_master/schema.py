"""Record schemas: how records are featurized, compared field by field, and blocked.

A Schema bundles
  * featurize(df)  -> list of per-record feature dicts (tokens, phonetic keys, normalised codes ...)
  * compare(fa, fb) -> tuple of comparison levels, one entry per field in `fields` (None = a value is missing)
  * block_rules     -> candidate-generation rules (see blocking.py)

Two schemas are defined: CUSTOMER (this project's records) and FEBRL (the public benchmark), so the same
matchers run on both without any benchmark-specific logic inside the matchers.

Comparison levels are ordinal integers; a higher level means stronger agreement. Level 0 always means "values
present but different". Missing values compare as None and carry NO weight in the probabilistic model.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from customer_master.phonetic import indic_key
from customer_master.similarity import jaro_winkler, within_one_edit


@dataclass(frozen=True)
class BlockRule:
    name: str
    keys: Callable[[dict], list]  # feature dict -> list of block keys (empty list = record skipped by this rule)
    fields: frozenset  # comparison fields whose agreement is implied by the rule (excluded when estimating m)
    max_block: int = 200  # blocks larger than this are skipped (and counted) to bound the pair explosion


@dataclass
class Schema:
    name: str
    fields: list  # [(field_name, n_levels)]
    featurize: Callable[[pd.DataFrame], list]
    compare: Callable[[dict, dict], tuple]
    block_rules: list = field(default_factory=list)

    @property
    def field_names(self) -> list:
        return [f for f, _ in self.fields]

    @property
    def n_levels(self) -> list:
        return [n for _, n in self.fields]


# ----------------------------------------------------------------------------- shared comparators
def token_level(a: str | None, b: str | None) -> int | None:
    """Name-token agreement: 3 exact, 2 initial / abbreviation / same phonetic key, 1 Jaro-Winkler >= 0.88 or a single
    typo in a name of 4+ letters, 0 other."""
    if not a or not b:
        return None
    if a == b:
        return 3
    if len(a) == 1 or len(b) == 1:
        return 2 if a[0] == b[0] else 0
    if (a.startswith(b) or b.startswith(a)) and min(len(a), len(b)) >= 2:
        return 2
    if indic_key(a) == indic_key(b):
        return 2
    if jaro_winkler(a, b) >= 0.88 or (min(len(a), len(b)) >= 4 and within_one_edit(a, b)):
        return 1
    return 0


def name_levels(fa: dict, fb: dict) -> tuple:
    """(first_level, last_level), choosing the better of straight and swapped (last, first) orientation."""
    direct = (token_level(fa["first"], fb["first"]), token_level(fa["last"], fb["last"]))
    swapped = (token_level(fa["first"], fb["last"]), token_level(fa["last"], fb["first"]))

    def total(t):
        return sum(x for x in t if x is not None)

    return swapped if total(swapped) > total(direct) and None not in swapped else direct


def code_level(a: str | None, b: str | None) -> int | None:
    """Identifier agreement: 2 exact, 1 one typo (substitution / transposition / drop), 0 different."""
    if not a or not b:
        return None
    if a == b:
        return 2
    return 1 if within_one_edit(a, b) else 0


def text_level(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    if a == b:
        return 2
    return 1 if jaro_winkler(a, b) >= 0.9 else 0


def exact_level(a, b) -> int | None:
    if a is None or b is None or a == "" or b == "":
        return None
    return int(a == b)


def _s(v) -> str | None:
    return None if v is None or (isinstance(v, float) and v != v) or str(v).strip() == "" else str(v).strip()


# ----------------------------------------------------------------------------- CUSTOMER schema
def _name_features(full_name: str | None) -> dict:
    toks = full_name.lower().split() if full_name else []
    if len(toks) < 2:
        return {"first": None, "last": None}
    return {"first": toks[0], "last": toks[-1]}


def featurize_customer(df: pd.DataFrame) -> list:
    feats = []
    for r in df.itertuples(index=False):
        f = {"id": r.record_id, "source": r.source_system, **_name_features(_s(r.full_name))}
        f["phone"], f["email"], f["city"], f["pincode"], f["date"] = (
            _s(r.phone),
            _s(r.email),
            _s(r.city),
            _s(r.pincode),
            _s(r.signup_date),
        )
        try:
            f["date_ord"] = dt.date.fromisoformat(f["date"]).toordinal() if f["date"] else None
        except ValueError:
            f["date_ord"] = None
        user = f["email"].split("@")[0] if f["email"] else ""
        letters = re.sub(r"[^a-z]", "", user)
        f["email_user"] = letters if len(letters) >= 5 else None
        f["ph_first"] = indic_key(f["first"]) if f["first"] else None
        f["ph_last"] = indic_key(f["last"]) if f["last"] else None
        feats.append(f)
    return feats


def email_level(fa: dict, fb: dict) -> int | None:
    if not fa["email"] or not fb["email"]:
        return None
    if fa["email"] == fb["email"]:
        return 2
    return 1 if fa["email_user"] and fa["email_user"] == fb["email_user"] else 0


def date_level(fa: dict, fb: dict) -> int | None:
    """Signup-date agreement: 2 same day, 1 within a year (a customer often appears in a second system soon after),
    0 otherwise."""
    if fa["date_ord"] is None or fb["date_ord"] is None:
        return None
    gap = abs(fa["date_ord"] - fb["date_ord"])
    return 2 if gap == 0 else 1 if gap <= 365 else 0


CUSTOMER_FIELDS = [("first", 4), ("last", 4), ("phone", 3), ("email", 3), ("city", 2), ("pincode", 2), ("date", 3)]


def compare_customer(fa: dict, fb: dict) -> tuple:
    first, last = name_levels(fa, fb)
    return (
        first,
        last,
        code_level(fa["phone"], fb["phone"]),
        email_level(fa, fb),
        exact_level(fa["city"], fb["city"]),
        exact_level(fa["pincode"], fb["pincode"]),
        date_level(fa, fb),
    )


def _single(field_name):
    return lambda f: [f[field_name]] if f[field_name] else []


def _name_keys(f: dict) -> list:
    """Two orientation-free keys per record: (phonetic last, first initial) and (phonetic first, last initial).
    A swapped 'Sharma Rahul' emits the same two keys as 'Rahul Sharma', so both meet in one block."""
    if not f["first"] or not f["last"]:
        return []
    return [(f["ph_last"], f["first"][0]), (f["ph_first"], f["last"][0])]


def _name_pin(f):
    return [k + (f["pincode"],) for k in _name_keys(f)] if f["pincode"] else []


def _name_city(f):
    return [k + (f["city"],) for k in _name_keys(f)] if f["city"] else []


def _phone_prefix(f):
    return [f["phone"][:5]] if f["phone"] else []


def _phone_suffix(f):
    return [f["phone"][-5:]] if f["phone"] else []


NAME_FIELDS = frozenset({"first", "last"})
CUSTOMER_BLOCKS = [
    BlockRule("phone", _single("phone"), frozenset({"phone"})),
    BlockRule("email", _single("email"), frozenset({"email"})),
    BlockRule("phone-prefix5", _phone_prefix, frozenset({"phone"})),
    BlockRule("phone-suffix5", _phone_suffix, frozenset({"phone"})),
    BlockRule("name+pincode", _name_pin, NAME_FIELDS | {"pincode"}, max_block=300),
    BlockRule("name+city", _name_city, NAME_FIELDS | {"city"}, max_block=120),
]
CUSTOMER = Schema("customer", CUSTOMER_FIELDS, featurize_customer, compare_customer, CUSTOMER_BLOCKS)


# ----------------------------------------------------------------------------- FEBRL schema (public benchmark)
FEBRL_FIELDS = [
    ("first", 4), ("last", 4), ("soc_sec_id", 3), ("dob", 3), ("address", 3), ("suburb", 3), ("postcode", 3),
    ("state", 2),
]  # fmt: skip


def featurize_febrl(df: pd.DataFrame) -> list:
    feats = []
    for r in df.to_dict("records"):
        f = {
            "id": r["record_id"],
            "first": _s(r["given_name"]),
            "last": _s(r["surname"]),
            "soc_sec_id": _s(r["soc_sec_id"]),
            "dob": _s(r["date_of_birth"]),
            "suburb": _s(r["suburb"]),
            "postcode": _s(r["postcode"]),
            "state": _s(r["state"]),
        }
        street = " ".join(x for x in (_s(r["street_number"]), _s(r["address_1"])) if x)
        f["address"] = street or None
        f["ph_first"] = indic_key(f["first"]) if f["first"] else None
        f["ph_last"] = indic_key(f["last"]) if f["last"] else None
        f["pincode"] = f["postcode"]  # shared name used by the generic name+pincode rule
        f["city"] = f["suburb"]
        feats.append(f)
    return feats


def compare_febrl(fa: dict, fb: dict) -> tuple:
    first, last = name_levels(fa, fb)
    return (
        first,
        last,
        code_level(fa["soc_sec_id"], fb["soc_sec_id"]),
        code_level(fa["dob"], fb["dob"]),
        text_level(fa["address"], fb["address"]),
        text_level(fa["suburb"], fb["suburb"]),
        code_level(fa["postcode"], fb["postcode"]),
        exact_level(fa["state"], fb["state"]),
    )


FEBRL_BLOCKS = [
    BlockRule("soc_sec_id", _single("soc_sec_id"), frozenset({"soc_sec_id"})),
    BlockRule("dob", _single("dob"), frozenset({"dob"}), max_block=50),
    BlockRule("name+postcode", _name_pin, NAME_FIELDS | {"postcode"}, max_block=300),
    BlockRule("name+suburb", _name_city, NAME_FIELDS | {"suburb"}, max_block=300),
]
FEBRL = Schema("febrl", FEBRL_FIELDS, featurize_febrl, compare_febrl, FEBRL_BLOCKS)
