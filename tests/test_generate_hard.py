import importlib
import tempfile
from pathlib import Path

import pandas as pd

from customer_master.generate_hard import HardConfig, generate_hard
from customer_master.profile import load_raw_sources


def gen(seed, **kw):
    t = tempfile.mkdtemp()
    cfg = generate_hard(Path(t), seed=seed, n_entities=800, **kw)
    return Path(t), cfg


def test_same_seed_is_identical_and_different_seed_differs():
    a, _ = gen(5)
    b, _ = gen(5)
    c, _ = gen(6)
    assert (a / "crm.csv").read_bytes() == (b / "crm.csv").read_bytes()
    assert (a / "answer_key.csv").read_bytes() == (b / "answer_key.csv").read_bytes()
    assert (a / "crm.csv").read_bytes() != (c / "crm.csv").read_bytes()


def test_answer_key_covers_every_record_and_has_duplicates():
    d, _ = gen(7)
    raw = load_raw_sources(d)
    key = pd.read_csv(d / "answer_key.csv", dtype=str)
    assert set(key.record_id) == set(raw.record_id)
    sizes = key.groupby("entity_id").size()
    assert sizes.max() >= 3 and (sizes > 1).mean() > 0.15


def test_hard_mode_is_actually_harder_than_the_defaults():
    d, cfg = gen(8)
    raw = load_raw_sources(d)
    blank_name = raw.full_name.str.strip().str.lower().isin(["", "n/a", "unknown", "customer"]).mean()
    assert 0.07 < blank_name < 0.16  # configured 10%
    # many different people share the same name text (common names)
    top = raw.full_name.str.lower().str.replace(",", "").str.split().map(lambda t: " ".join(sorted(t))).value_counts()
    assert top.iloc[0] >= 8
    assert cfg.p_dup_both_changed > 0.1


def test_generation_config_is_written_next_to_the_data():
    d, cfg = gen(9)
    assert (d / "generation_config.json").exists()
    assert HardConfig().seed == 101  # dev seed is the default


def test_default_generator_output_is_unchanged():
    """The easy generator must keep producing the committed data/raw files byte for byte."""
    from customer_master import common, generate_data

    importlib.reload(generate_data)  # reseed the module-level RNG
    with tempfile.TemporaryDirectory() as t:
        raw, key = Path(t) / "raw", Path(t) / "gt" / "answer_key.csv"
        generate_data.RAW_DIR, generate_data.GROUND_TRUTH_PATH = raw, key
        generate_data.main()
        for name in ("crm.csv", "web.csv", "sales.csv"):
            assert (raw / name).read_bytes() == (common.RAW_DIR / name).read_bytes()
        assert key.read_bytes() == common.GROUND_TRUTH_PATH.read_bytes()
    importlib.reload(generate_data)
