"""Data-quality monitoring: append the six rule pass rates of each run to a history CSV and flag drops."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HISTORY_COLUMNS = ["run_id", "run_at", "stage", "records", "R1", "R2", "R3", "R4", "R5", "R6"]


def load_settings(path: Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if cfg["max_drop_pp"] < 0:
        raise ValueError("max_drop_pp must be >= 0")
    return cfg


def current_rates(validation: pd.DataFrame, stage: str, rules: list) -> dict:
    """Pass rate (0-1) per rule from outputs/validation_summary.csv at the given stage (raw, std or golden)."""
    col = f"{stage}_pass_rate"
    if col not in validation.columns:
        raise ValueError(f"unknown stage {stage!r}; expected raw, std or golden")
    by_rule = validation.set_index("rule_id")
    missing = [r for r in rules if r not in by_rule.index]
    if missing:
        raise ValueError(f"rules missing from validation summary: {missing}")
    return {r: float(by_rule.loc[r, col]) for r in rules}


def append_run(history_path: Path, rates: dict, records: int, stage: str, run_id: str | None = None) -> pd.DataFrame:
    """Append one row and return the full history. A repeated run_id replaces its earlier row (idempotent re-runs)."""
    now = datetime.now(timezone.utc)
    run_id = run_id or now.strftime("%Y%m%dT%H%M%SZ")
    row = {"run_id": run_id, "run_at": now.isoformat(timespec="seconds"), "stage": stage, "records": records, **rates}
    path = Path(history_path)
    rows = pd.read_csv(path).to_dict("records") if path.exists() else []
    rows = [r for r in rows if str(r["run_id"]) != run_id] + [row]
    hist = pd.DataFrame(rows)[HISTORY_COLUMNS]
    hist.to_csv(path, index=False)
    return hist


def find_drops(history: pd.DataFrame, rules: list, max_drop_pp: float) -> list:
    """Rules whose latest pass rate fell by more than max_drop_pp points versus the previous run."""
    if len(history) < 2:
        return []
    prev, last = history.iloc[-2], history.iloc[-1]
    drops = []
    for r in rules:
        change = (float(last[r]) - float(prev[r])) * 100
        if change < -max_drop_pp:
            drops.append(
                {"rule": r, "previous": float(prev[r]), "latest": float(last[r]), "change_pp": round(change, 2)}
            )
    return drops


def plot_history(history: pd.DataFrame, rules: list, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 3.6))
    x = range(1, len(history) + 1)
    for r in rules:
        ax.plot(x, history[r].astype(float) * 100, marker="o", label=r)
    ax.set_xlabel("run #")
    ax.set_ylabel("pass rate (%)")
    ax.set_title(f"Rule pass rates per run ({history.stage.iloc[-1]} records)")
    ax.set_xticks(list(x))
    ax.legend(ncol=6, fontsize=8, loc="lower center")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
