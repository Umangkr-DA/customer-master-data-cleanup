"""Hard-mode synthetic data: adversarial noise for honest matcher evaluation.

The default generator (generate_data.py) is intentionally easy: phones and emails are almost unique and
duplicates differ only in formatting. Hard mode adds the failure modes real customer data has:

* phone typos (transposed / substituted / dropped digits), email typos
* nickname and spelling variants (Mohammed / Mohd / Muhammad), name typos, swapped first/last order, initials
* common names: names and cities are drawn from a skewed (Zipf-like) distribution and pincodes from a narrow
  range, so many DIFFERENT people share a name + city + pincode
* households sharing a phone and sometimes an email; "namesakes" (identical name, same phone, different people)
* recycled phone numbers re-assigned to unrelated people
* higher blank rates for name, phone, email
* duplicates where BOTH phone and email changed, or the customer moved (city + pincode changed)

All rates live in HardConfig and are written next to every dataset (generation_config.json). The matchers were
written against the dev dataset only; the held-out dataset uses a different seed and the same generator.
Because the noise PROCESS is shared, held-out measures generalisation across samples, not across noise models;
docs/limitations.md says so.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from customer_master.common import CITIES, CITY_PIN_PREFIX

SOURCES = ["CRM", "Web", "Sales"]
COLUMNS = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]
DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com"]

# ordered head -> tail; ranks feed a Zipf-like weight so the head names are very common
FIRST_HEAD = """Rahul Amit Priya Mohammed Pooja Rohit Neha Sandeep Anita Rajesh Sunita Vikas Deepak Anil Suresh
Sanjay Kavita Ankit Manish Divya Sachin Ravi Ramesh Kiran Ayesha Imran Gurpreet Harpreet Lakshmi Venkat
Srinivas""".split()
FIRST_TAIL = """Aarav Aditya Akash Arjun Dev Gaurav Harsh Ishaan Karan Kunal Mohit Naveen Nikhil Pankaj Pranav Tarun
Vivek Yash Ananya Anjali Aditi Isha Kavya Meera Riya Shreya Sneha Swati Tanvi Usha Varsha Nisha Komal Preeti Rekha
Shweta Jyoti Farhan Salman Zoya Fatima Sana Irfan Shabnam Rizwan Jaspreet Manpreet Krishna Suman""".split()
LAST_HEAD = """Sharma Singh Kumar Patel Gupta Khan Verma Yadav Mishra Joshi Reddy Jain Das Shah Rao Kaur Ansari
Mehta Pandey Tiwari""".split()
LAST_TAIL = """Dubey Chauhan Rathore Saxena Agarwal Kapoor Malhotra Khanna Bansal Garg Goyal Mittal Sinha Thakur Nair
Iyer Menon Pillai Bose Ghosh Banerjee Chatterjee Kulkarni Deshmukh Patil Shinde Pawar Jadhav Bhatt Desai Trivedi
Solanki Parmar Chaudhary Rawat Bisht Negi Kohli Arora Sethi Bhatia Tandon Dixit Shukla Awasthi Srivastava Siddiqui
Qureshi Sheikh Naidu Hegde Gill""".split()

# spelling / nickname variants applied at record level (the canonical form is the key)
VARIANTS = {
    "Mohammed": ["Mohd", "Muhammad", "Mohamed", "Mohamad", "Md"],
    "Rahul": ["Rahool", "Rahull"],
    "Sandeep": ["Sandip", "Sundeep"],
    "Deepak": ["Dipak", "Deepuk"],
    "Vikas": ["Vikash", "Vickas"],
    "Priya": ["Pria", "Priyaa"],
    "Pooja": ["Puja", "Poojaa"],
    "Suresh": ["Suresh", "Sures"],
    "Kavita": ["Kavitha", "Kaveeta"],
    "Anil": ["Anill", "Aneel"],
    "Rajesh": ["Rajes", "Rajesh"],
    "Ravi": ["Ravee", "Rawi"],
    "Sunita": ["Sunitha", "Suneeta"],
    "Neha": ["Nehaa", "Neeha"],
    "Gurpreet": ["Gurpreet", "Gurprit"],
    "Harpreet": ["Harprit", "Harpreet"],
    "Imran": ["Emran", "Imraan"],
    "Ayesha": ["Aisha", "Ayesa"],
    "Lakshmi": ["Laxmi", "Lakshmy"],
    "Srinivas": ["Srinivasa", "Sreenivas"],
    "Venkat": ["Venkata", "Venkatt"],
    "Kumar": ["Kumaar", "Kr"],
    "Singh": ["Sing", "Sinh"],
    "Sharma": ["Sarma", "Shrma"],
}


@dataclass
class HardConfig:
    """Every knob of the hard generator. Defaults define 'hard mode' for the reported experiments."""

    seed: int = 101
    n_entities: int = 4000
    p_extra_1: float = 0.18  # entity gets 1 extra record
    p_extra_2: float = 0.04  # entity gets 2 extra records
    p_extra_3: float = 0.01  # entity gets 3 extra records
    zipf_first: float = 0.9  # exponent for first-name popularity
    zipf_last: float = 0.9
    zipf_city: float = 0.7
    pin_suffix_max: int = 90  # pincode = city prefix + 001..pin_suffix_max (narrow -> many shared pincodes)
    p_household: float = 0.06  # entity joins a household of an earlier entity (shares phone, maybe email)
    p_household_email: float = 0.35
    n_namesake_pairs: int = 8  # identical name + phone + pincode, DIFFERENT people (irreducible ambiguity)
    p_recycled_phone: float = 0.015
    # record-level noise (applied to every record, original and duplicate)
    p_name_blank: float = 0.10
    p_phone_blank: float = 0.10
    p_email_blank: float = 0.12
    p_name_variant: float = 0.12
    p_name_typo: float = 0.06
    p_name_swap: float = 0.05
    p_name_initial: float = 0.05
    p_phone_transpose: float = 0.03
    p_phone_substitute: float = 0.02
    p_phone_drop: float = 0.02
    p_email_typo: float = 0.03
    p_format_noise: float = 0.30
    # duplicate-record changes
    p_dup_both_changed: float = 0.15
    p_dup_email_changed: float = 0.25
    p_dup_phone_changed: float = 0.15
    p_dup_moved: float = 0.10
    # a customer rarely signs up in two systems on the same day: duplicate records usually carry a later date
    p_dup_same_signup: float = 0.30
    max_signup_shift_days: int = 540
    notes: dict = field(default_factory=dict)


def _zipf_weights(n: int, s: float) -> list[float]:
    return [1.0 / (rank + 1) ** s for rank in range(n)]


class HardGenerator:
    def __init__(self, cfg: HardConfig):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.firsts = FIRST_HEAD + FIRST_TAIL
        self.lasts = LAST_HEAD + LAST_TAIL
        self.w_first = _zipf_weights(len(self.firsts), cfg.zipf_first)
        self.w_last = _zipf_weights(len(self.lasts), cfg.zipf_last)
        self.w_city = _zipf_weights(len(CITIES), cfg.zipf_city)

    # ---------------------------------------------------------------- entities
    def rand_phone(self) -> str:
        r = self.rng
        return r.choice("6789") + "".join(r.choice("0123456789") for _ in range(9))

    def make_entities(self) -> list[dict]:
        c, r = self.cfg, self.rng
        ents: list[dict] = []
        used_phone: set[str] = set()
        used_email: set[str] = set()
        span = (dt.date(2026, 9, 30) - dt.date(2016, 1, 1)).days
        for i in range(c.n_entities):
            first = r.choices(self.firsts, self.w_first)[0]
            last = r.choices(self.lasts, self.w_last)[0]
            city = r.choices(CITIES, self.w_city)[0]
            pin = CITY_PIN_PREFIX[city] + f"{r.randint(1, c.pin_suffix_max):03d}"
            while True:
                phone = self.rand_phone()
                if phone not in used_phone and len(set(phone)) > 1:
                    used_phone.add(phone)
                    break
            while True:
                email = f"{first.lower()}.{last.lower()}{r.randint(1, 9999)}@{r.choice(DOMAINS)}"
                if email not in used_email:
                    used_email.add(email)
                    break
            d = dt.date(2016, 1, 1) + dt.timedelta(days=r.randint(0, span))
            ents.append(
                dict(
                    entity_id=f"E{i + 1:05d}",
                    first=first,
                    last=last,
                    phone=phone,
                    email=email,
                    city=city,
                    pincode=pin,
                    signup_date=d,
                    kind="plain",
                )
            )
        # households: share phone (and sometimes email), same surname + city + pincode, different first names
        for b in range(c.n_entities):
            if b > 0 and r.random() < c.p_household:
                a = r.randrange(0, b)
                A, B = ents[a], ents[b]
                B["last"], B["city"], B["pincode"] = A["last"], A["city"], A["pincode"]
                if B["first"] == A["first"]:
                    B["first"] = r.choice([f for f in self.firsts if f != A["first"]])
                B["phone"] = A["phone"]
                if r.random() < c.p_household_email:
                    B["email"] = A["email"]
                else:
                    B["email"] = f"{B['first'].lower()}.{B['last'].lower()}{r.randint(1, 9999)}h@{r.choice(DOMAINS)}"
                B["kind"] = "household"
        # namesakes: same first+last, same phone and pincode, different people (e.g. father and son)
        pool = [i for i, e in enumerate(ents) if e["kind"] == "plain"]
        for _ in range(c.n_namesake_pairs):
            a, b = r.sample(pool, 2)
            A, B = ents[a], ents[b]
            B["first"], B["last"], B["city"], B["pincode"], B["phone"] = (
                A["first"],
                A["last"],
                A["city"],
                A["pincode"],
                A["phone"],
            )
            B["email"] = f"{B['first'].lower()}{r.randint(1, 99)}.{B['last'].lower()}@{r.choice(DOMAINS)}"
            A["kind"] = B["kind"] = "namesake"
            pool.remove(a)
            pool.remove(b)
        # recycled phones: an unrelated entity ends up with a number someone else used earlier
        for b in range(c.n_entities):
            if ents[b]["kind"] == "plain" and r.random() < c.p_recycled_phone:
                a = r.randrange(c.n_entities)
                if a != b and ents[a]["last"] != ents[b]["last"]:
                    ents[b]["phone"] = ents[a]["phone"]
                    ents[b]["kind"] = "recycled"
        return ents

    # ---------------------------------------------------------------- noise on one field
    def _typo(self, s: str) -> str:
        r = self.rng
        if len(s) < 4:
            return s
        i = r.randrange(1, len(s) - 1)
        kind = r.choice(["drop", "swap", "sub"])
        if kind == "drop":
            return s[:i] + s[i + 1 :]
        if kind == "swap":
            return s[:i] + s[i + 1] + s[i] + s[i + 2 :]
        return s[:i] + r.choice("aeioulnrst") + s[i + 1 :]

    def noisy_name(self, first: str, last: str) -> str:
        c, r = self.cfg, self.rng
        u = r.random()
        if u < c.p_name_blank:
            return r.choice(["", "N/A", "Unknown", "customer"])
        edges = [c.p_name_variant, c.p_name_typo, c.p_name_swap, c.p_name_initial]
        t = u - c.p_name_blank
        f, l_ = first, last
        order_swapped = False
        if t < edges[0]:
            f = r.choice(VARIANTS.get(first, [self._typo(first)]))
            if r.random() < 0.3:
                l_ = r.choice(VARIANTS.get(last, [last]))
        elif t < sum(edges[:2]):
            if r.random() < 0.7:
                f = self._typo(first)
            else:
                l_ = self._typo(last)
        elif t < sum(edges[:3]):
            order_swapped = True
        elif t < sum(edges):
            f = first[0]
        name = f"{l_} {f}" if order_swapped else f"{f} {l_}"
        if r.random() < c.p_format_noise:
            style = r.choice(["upper", "lower", "space", "title", "comma", "trail"])
            if style == "upper":
                name = name.upper()
            elif style == "lower":
                name = name.lower()
            elif style == "space":
                name = f"  {name.replace(' ', '   ')} "
            elif style == "title":
                name = f"{r.choice(['Mr.', 'Mrs.', 'Ms.', 'Dr.'])} {name}"
            elif style == "comma" and not order_swapped:
                name = f"{l_}, {f}"
            else:
                name = name + " "
        return name

    def noisy_phone(self, p: str) -> str:
        c, r = self.cfg, self.rng
        u = r.random()
        if u < c.p_phone_blank:
            return r.choice(["", "NA", "1234567890", "0000000000"])
        u -= c.p_phone_blank
        if u < c.p_phone_transpose:
            i = r.randrange(0, 9)
            q = p[:i] + p[i + 1] + p[i] + p[i + 2 :]
            return q if q != p else p[:9] + str((int(p[9]) + 1) % 10)
        u -= c.p_phone_transpose
        if u < c.p_phone_substitute:
            i = r.randrange(1, 10)
            return p[:i] + str((int(p[i]) + r.randint(1, 9)) % 10) + p[i + 1 :]
        u -= c.p_phone_substitute
        if u < c.p_phone_drop:
            i = r.randrange(0, 10)
            return p[:i] + p[i + 1 :]
        if r.random() < c.p_format_noise:
            return r.choice(
                [
                    f"+91 {p[:5]} {p[5:]}",
                    f"+91-{p[:5]}-{p[5:]}",
                    f"0{p}",
                    f"{p[:5]}-{p[5:]}",
                    f"(+91) {p}",
                    f"91{p}",
                    f"{p[:3]} {p[3:6]} {p[6:]}",
                ]
            )
        return p

    def noisy_email(self, e: str) -> str:
        c, r = self.cfg, self.rng
        u = r.random()
        if u < c.p_email_blank:
            return r.choice(["", "NA", "test@test"])
        if u < c.p_email_blank + c.p_email_typo:
            user, dom = e.split("@")
            return f"{self._typo(user)}@{dom}"
        if r.random() < c.p_format_noise:
            user, dom = e.split("@")
            typo = {
                "gmail.com": "gmail.con",
                "yahoo.com": "yahho.com",
                "outlook.com": "outlok.com",
                "hotmail.com": "hotmial.com",
                "rediffmail.com": "rediffmail.co",
            }[dom]
            return r.choice([e.upper(), f" {e} ", f"{user}@{typo}", e.capitalize()])
        return e

    def noisy_city(self, city: str) -> str:
        r = self.rng
        u = r.random()
        if u < 0.07:
            return r.choice(["", "N/A", "Unknown"])
        if u < 0.07 + 0.25:
            alias = {
                "Bengaluru": ["Bangalore", "Bengalooru"],
                "Mumbai": ["Bombay"],
                "Kolkata": ["Calcutta"],
                "Chennai": ["Madras"],
                "Bhopal": ["Bhopal MP", "BPL"],
                "Delhi": ["New Delhi"],
            }
            return r.choice(alias.get(city, [city.upper(), city.lower(), f" {city} "]))
        return city

    def noisy_pin(self, p: str) -> str:
        r = self.rng
        u = r.random()
        if u < 0.08:
            return r.choice([p[:5], "000000", ""])
        if u < 0.08 + 0.25:
            return r.choice([f"{p[:3]} {p[3:]}", f"{p[:3]}-{p[3:]}", f"{p}.0"])
        return p

    def noisy_date(self, d: dt.date) -> str:
        r = self.rng
        u = r.random()
        if u < 0.06:
            return r.choice(["31/02/2021", "TBD", ""])
        if u < 0.06 + 0.3:
            return r.choice([d.strftime("%d/%m/%Y"), d.strftime("%d-%b-%Y"), d.strftime("%Y/%m/%d")])
        return d.isoformat()

    # ---------------------------------------------------------------- dataset
    def generate(self) -> tuple[dict[str, list[dict]], list[tuple[str, str]]]:
        c, r = self.cfg, self.rng
        ents = self.make_entities()
        plan: list[tuple[dict, str, bool]] = []
        for e in ents:
            plan.append((e, r.choice(SOURCES), False))
            u = r.random()
            n_extra = (
                3
                if u < c.p_extra_3
                else 2
                if u < c.p_extra_3 + c.p_extra_2
                else 1
                if u < (c.p_extra_3 + c.p_extra_2 + c.p_extra_1)
                else 0
            )
            for _ in range(n_extra):
                plan.append((e, r.choice(SOURCES), True))
        r.shuffle(plan)

        rows: dict[str, list[dict]] = {s: [] for s in SOURCES}
        key: list[tuple[str, str]] = []
        counter = {s: 0 for s in SOURCES}
        for e, src, is_dup in plan:
            phone, email, city, pin = e["phone"], e["email"], e["city"], e["pincode"]
            first, last = e["first"], e["last"]
            if is_dup:
                u = r.random()
                if u < c.p_dup_both_changed:
                    phone = self.rand_phone()
                    email = f"{first.lower()}{r.randint(10000, 99999)}@{r.choice(DOMAINS)}"
                elif u < c.p_dup_both_changed + c.p_dup_email_changed:
                    email = f"{first.lower()}{last[0].lower()}{r.randint(10, 99)}@{r.choice(DOMAINS)}"
                elif u < c.p_dup_both_changed + c.p_dup_email_changed + c.p_dup_phone_changed:
                    phone = self.rand_phone()
                if r.random() < c.p_dup_moved:
                    city = r.choice(CITIES)
                    pin = CITY_PIN_PREFIX[city] + f"{r.randint(1, c.pin_suffix_max):03d}"
            signup = e["signup_date"]
            if is_dup and r.random() >= c.p_dup_same_signup:
                signup = min(signup + dt.timedelta(days=r.randint(1, c.max_signup_shift_days)), dt.date(2026, 9, 30))
            counter[src] += 1
            rid = f"{src.upper()}-{counter[src]:05d}"
            rows[src].append(
                dict(
                    record_id=rid,
                    source_system=src,
                    full_name=self.noisy_name(first, last),
                    phone=self.noisy_phone(phone),
                    email=self.noisy_email(email),
                    city=self.noisy_city(city),
                    pincode=self.noisy_pin(pin),
                    signup_date=self.noisy_date(signup),
                )
            )
            key.append((rid, e["entity_id"]))
        return rows, key


def write_dataset(out_dir: Path, rows: dict, key: list, cfg: HardConfig) -> None:
    """Layout: <out_dir>/{crm,web,sales}.csv, answer_key.csv (evaluation only), generation_config.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in SOURCES:
        with open(out_dir / f"{s.lower()}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, COLUMNS)
            w.writeheader()
            w.writerows(rows[s])
    with open(out_dir / "answer_key.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "entity_id"])
        w.writerows(key)
    (out_dir / "generation_config.json").write_text(json.dumps(asdict(cfg), indent=2) + "\n", encoding="utf-8")


def generate_hard(out_dir: Path, **overrides) -> HardConfig:
    cfg = HardConfig(**overrides)
    rows, key = HardGenerator(cfg).generate()
    write_dataset(Path(out_dir), rows, key, cfg)
    return cfg
