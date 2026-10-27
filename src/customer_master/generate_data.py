"""Step 0 - generate synthetic, deliberately messy customer data from 3 source systems (CRM, Web, Sales).

Writes data/raw/{crm,web,sales}.csv and data/ground_truth/answer_key.csv (record_id -> true entity_id).
The answer key is NEVER read by the cleaning/matching code - only by the evaluation in step 5.
Randomness is fully seeded (SEED = 42), so every run produces identical files.
"""

import csv
import random
import datetime as dt
from customer_master.common import CITY_PIN_PREFIX, CITIES, RAW_DIR, GROUND_TRUTH_PATH, SEED, get_logger

N_ENTITIES = 4517  # true distinct customers
N_DUP_RECORDS = 702  # extra records created by duplication across systems -> 5,219 rows
P_FIX = 0.30  # per-field chance of a *fixable* formatting problem
P_BAD = 0.05  # per-field chance of an *unfixable* value
SOURCES = ["CRM", "Web", "Sales"]
log = get_logger("generate")

FIRST = """Aarav Aditya Akash Amit Anil Ankit Arjun Deepak Dev Gaurav Harsh Ishaan Karan Kunal Manish Mohit
Naveen Nikhil Pankaj Pranav Rahul Rajesh Rohit Sachin Sandeep Sanjay Siddharth Suresh Tarun Vikas
Vivek Yash Ananya Anjali Aditi Divya Isha Kavya Meera Neha Pooja Priya Riya Shreya Sneha Sunita
Swati Tanvi Usha Varsha Nisha Komal Preeti Rekha Shweta Anita Jyoti Kiran""".split()
LAST = """Sharma Verma Gupta Singh Patel Mehta Joshi Yadav Mishra Tiwari Pandey Dubey Chauhan Rathore Saxena
Agarwal Jain Kapoor Malhotra Khanna Bansal Garg Goyal Mittal Sinha Thakur Reddy Nair Iyer Menon
Pillai Das Bose Ghosh Banerjee Chatterjee Kulkarni Deshmukh Patil Shinde Pawar Jadhav Bhatt Desai
Shah Trivedi Solanki Parmar Chaudhary Rawat Bisht Negi Kohli Arora Sethi Bhatia Tandon Dixit Shukla
Awasthi Srivastava""".split()
DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com"]

rng = random.Random(SEED)


def rand_phone():
    return rng.choice("6789") + "".join(rng.choice("0123456789") for _ in range(9))


def make_entities():
    used_phone, used_email, ents = set(), set(), []
    for i in range(N_ENTITIES):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        while True:
            phone = rand_phone()
            if phone not in used_phone and len(set(phone)) > 1:
                used_phone.add(phone)
                break
        while True:
            email = f"{first.lower()}.{last.lower()}{rng.randint(1, 9999)}@{rng.choice(DOMAINS)}"
            if email not in used_email:
                used_email.add(email)
                break
        city = rng.choice(CITIES)
        pin = CITY_PIN_PREFIX[city] + f"{rng.randint(1, 999):03d}"
        span = (dt.date(2026, 9, 30) - dt.date(2016, 1, 1)).days
        d = dt.date(2016, 1, 1) + dt.timedelta(days=rng.randint(0, span))
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
            )
        )
    # decoys: 25 relatives who share a phone but are DIFFERENT people (same surname, different first name)
    for _ in range(25):
        a, b = rng.sample(range(N_ENTITIES), 2)
        ents[b]["last"] = ents[a]["last"]
        if ents[b]["first"] == ents[a]["first"]:
            ents[b]["first"] = "Kiran" if ents[a]["first"] != "Kiran" else "Usha"
        ents[b]["phone"] = ents[a]["phone"]
        ents[b]["email"] = (
            f"{ents[b]['first'].lower()}.{ents[b]['last'].lower()}{rng.randint(1, 9999)}x@{rng.choice(DOMAINS)}"
        )
    return ents


# ---------------- noise injectors ----------------
def noisy_name(first, last):
    r = rng.random()
    if r < P_BAD:
        return rng.choice(["", "N/A", "Unknown", "customer"])
    if r < P_BAD + P_FIX:
        f = rng.choice(["upper", "lower", "space", "title", "comma", "trail"])
        n = f"{first} {last}"
        return {
            "upper": n.upper(),
            "lower": n.lower(),
            "space": f"  {first}   {last} ",
            "title": f"{rng.choice(['Mr.', 'Mrs.', 'Ms.', 'Dr.'])} {first} {last}",
            "comma": f"{last}, {first}",
            "trail": n + " ",
        }[f]
    return f"{first} {last}"


def noisy_phone(p):
    r = rng.random()
    if r < P_BAD:
        return rng.choice([p[:9], "NA", "1234567890", "0000000000", p[:5] + "xx" + p[7:]])
    if r < P_BAD + P_FIX:
        return rng.choice(
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


def noisy_email(e):
    r = rng.random()
    if r < P_BAD:
        return rng.choice([e.replace("@", ""), "NA", e.split("@")[0] + "@", "test@test", ""])
    if r < P_BAD + P_FIX:
        user, dom = e.split("@")
        typo = {
            "gmail.com": "gmail.con",
            "yahoo.com": "yahho.com",
            "outlook.com": "outlok.com",
            "hotmail.com": "hotmial.com",
            "rediffmail.com": "rediffmail.co",
        }[dom]
        return rng.choice([e.upper(), f" {e} ", f"{user}@{typo}", e.capitalize(), f"{user}@{dom.upper()}"])
    return e


def noisy_city(c):
    r = rng.random()
    if r < P_BAD:
        return rng.choice(["", "N/A", "Unknown", "XYZ"])
    if r < P_BAD + P_FIX:
        alias = {
            "Bengaluru": ["Bangalore", "Bengalooru"],
            "Mumbai": ["Bombay"],
            "Kolkata": ["Calcutta"],
            "Chennai": ["Madras"],
            "Bhopal": ["Bhopal MP", "Bhopal, M.P.", "BPL"],
            "Delhi": ["New Delhi"],
        }
        f = rng.choice(["upper", "lower", "space", "typo", "alias"])
        if f == "alias" and c in alias:
            return rng.choice(alias[c])
        if f == "typo":
            i = rng.randrange(1, len(c) - 1)
            return c[:i] + c[i + 1 :] if rng.random() < 0.5 else c[:i] + c[i] + c[i:]
        return {"upper": c.upper(), "lower": c.lower(), "space": f" {c} ", "alias": c.lower(), "typo": c}[f]
    return c


def noisy_pin(p):
    r = rng.random()
    if r < P_BAD:
        return rng.choice([p[:5], p[:5] + "A", "000000", ""])
    if r < P_BAD + P_FIX:
        return rng.choice([f"{p[:3]} {p[3:]}", f"{p[:3]}-{p[3:]}", f"{p}.0", f" {p} "])
    return p


def noisy_date(d):
    r = rng.random()
    if r < P_BAD:
        return rng.choice(["31/02/2021", "00/00/0000", "TBD", "2031-01-05", ""])
    if r < P_BAD + P_FIX:
        return rng.choice(
            [d.strftime("%d/%m/%Y"), d.strftime("%d-%b-%Y"), d.strftime("%d %B %Y"), d.strftime("%Y/%m/%d")]
        )
    return d.isoformat()


def main():
    ents = make_entities()
    # ~40 entities get two extra records (3 systems), the rest one extra
    slots = list(range(N_ENTITIES))
    rng.shuffle(slots)
    n_double = 40
    dup_entities = slots[: N_DUP_RECORDS - n_double] + slots[:n_double]

    base_src = {e["entity_id"]: rng.choice(SOURCES) for e in ents}
    plan = [(e, base_src[e["entity_id"]], False) for e in ents]
    used = {}
    for i in dup_entities:
        e = ents[i]
        taken = used.setdefault(e["entity_id"], {base_src[e["entity_id"]]})
        src = rng.choice([s for s in SOURCES if s not in taken])
        taken.add(src)
        plan.append((e, src, True))
    rng.shuffle(plan)

    rows = {s: [] for s in SOURCES}
    key, counter = [], {s: 0 for s in SOURCES}
    for e, src, is_dup in plan:
        phone, email, first, last = e["phone"], e["email"], e["first"], e["last"]
        if is_dup:
            r = rng.random()
            if r < 0.025:  # person changed BOTH phone and email -> not linkable (expected miss)
                phone = rand_phone()
                email = f"{first.lower()}{rng.randint(10000, 99999)}@{rng.choice(DOMAINS)}"
            elif r < 0.30:  # alternate email only
                email = f"{first.lower()}{last[0].lower()}{rng.randint(10, 99)}@{rng.choice(DOMAINS)}"
            elif r < 0.45:  # alternate phone only
                phone = rand_phone()
            if rng.random() < 0.05:  # first name shortened to an initial
                first = first[0]
        counter[src] += 1
        rid = f"{src.upper()}-{counter[src]:05d}"
        rows[src].append(
            dict(
                record_id=rid,
                source_system=src,
                full_name=noisy_name(first, last),
                phone=noisy_phone(phone),
                email=noisy_email(email),
                city=noisy_city(e["city"]),
                pincode=noisy_pin(e["pincode"]),
                signup_date=noisy_date(e["signup_date"]),
            )
        )
        key.append((rid, e["entity_id"]))

    out = RAW_DIR
    out.mkdir(parents=True, exist_ok=True)
    cols = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]
    for s in SOURCES:
        with open(out / f"{s.lower()}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, cols)
            w.writeheader()
            w.writerows(rows[s])
    GROUND_TRUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GROUND_TRUTH_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "entity_id"])
        w.writerows(key)
    log.info(
        "generated %s records (%s) from %s true customers",
        sum(len(v) for v in rows.values()),
        {s: len(rows[s]) for s in SOURCES},
        N_ENTITIES,
    )


if __name__ == "__main__":
    main()
