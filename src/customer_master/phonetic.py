"""Phonetic key for Indian (and generally romanised South-Asian) names.

Soundex/Metaphone are built for English. Romanised Indian names vary in vowel length (Deepak/Dipak), aspirates
(Sharma/Sarma, Vikash/Vikas), v/w and j/z, doubled consonants (Mohammed/Mohamed) and dropped vowels (Mohd).
The key keeps the first letter, rewrites common digraphs to one letter, drops later vowels and h, maps similar
consonants together, and collapses repeats. It is deliberately coarse: it is used for blocking and as ONE comparison
level whose reliability the Fellegi-Sunter model estimates from data, never as proof of identity.

    >>> indic_key("Mohammed") == indic_key("Mohd") == indic_key("Muhammad")
    True
    >>> indic_key("Deepak") == indic_key("Dipak")
    True
"""

from __future__ import annotations

import re
from functools import lru_cache

_DIGRAPHS = [
    ("ksh", "x"), ("chh", "c"), ("sh", "s"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("jh", "j"), ("th", "t"),
    ("dh", "d"), ("ph", "f"), ("bh", "b"), ("ck", "k"), ("ee", "i"), ("oo", "u"), ("aa", "a"), ("ii", "i"),
    ("uu", "u"), ("ou", "u"), ("ai", "e"), ("ay", "e"), ("ey", "e"),
]  # fmt: skip
_CONSONANT_MAP = str.maketrans({"w": "v", "z": "j", "c": "k", "q": "k", "x": "k", "f": "p", "y": "i"})
_VOWELS = set("aeiouyh")


@lru_cache(maxsize=100_000)
def indic_key(token: str) -> str:
    s = re.sub(r"[^a-z]", "", token.lower())
    if not s:
        return ""
    for a, b in _DIGRAPHS:
        s = s.replace(a, b)
    s = s.translate(_CONSONANT_MAP)
    head, tail = s[0], "".join(ch for ch in s[1:] if ch not in _VOWELS)
    out = [head]
    for ch in tail:
        if ch != out[-1]:
            out.append(ch)
    return "".join(out)
