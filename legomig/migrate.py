"""Stage runner: migrate table by table, idempotently and resumably.

* Each table is a stage. Its *fingerprint* is the hash of: schema version, the legacy
  files, the mapping, and the data hashes of the parent tables it depends on.
* If a stage's fingerprint matches what ``state.json`` recorded and its outputs exist,
  the stage is skipped — re-running is a no-op.
* Outputs are written to a temp location and swapped into place, and ``state.json``
  is updated only after that. If the run dies mid-way, the next run redoes only the
  unfinished stages.
* Output is sorted by primary key, so the same input always gives the same data
  (verified by the data hash, which ignores file-level metadata such as timestamps).
"""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from .schema import ORDER, SCHEMA_VERSION, TABLES
from .storage import read_table, write_table
from .transform import REJECT_COLUMNS, load_legacy, transform


class SimulatedCrash(RuntimeError):
    """Raised by --crash-after to demonstrate resuming."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def data_hash(df: pd.DataFrame) -> str:
    """Hash of the *content* (sorted, as CSV), independent of Parquet file metadata."""
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()


def _write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _write_rejects_atomic(rejects: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".csv.tmp")
    rejects.to_csv(tmp, index=False)
    os.replace(tmp, path)


def read_rejects(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=REJECT_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def run(legacy_dir: Path, out_dir: Path, mapping_path: Path, crash_after: str | None = None) -> dict:
    warehouse, rejects_dir = out_dir / "warehouse", out_dir / "rejects"
    warehouse.mkdir(parents=True, exist_ok=True)
    rejects_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    legacy_fp = sha256_file(legacy_dir / "catalog.json") + sha256_file(legacy_dir / "sets.jsonl")
    mapping_fp = sha256_file(mapping_path)

    legacy = None
    parents: dict = {}
    summary = {}
    for table in ORDER:
        deps = sorted({p for _, p, _ in TABLES[table]["fks"] if p != table})
        fingerprint = hashlib.sha256("|".join(
            [SCHEMA_VERSION, legacy_fp, mapping_fp] + [state[d]["data_hash"] for d in deps]
        ).encode()).hexdigest()
        rej_path = rejects_dir / f"{table}.csv"

        prev = state.get(table, {})
        if prev.get("fingerprint") == fingerprint and (warehouse / table).exists() and rej_path.exists():
            print(f"  {table:<16} up to date, skipped")
            summary[table] = {**prev, "action": "skipped"}
        else:
            if legacy is None:
                print("  loading legacy export…")
                legacy = load_legacy(legacy_dir)
            for d in deps:
                parents.setdefault(d, _parent_info(d, warehouse, rejects_dir))
            accepted, rejects = transform(table, legacy[table], mapping, parents)
            write_table(accepted, table, warehouse)
            _write_rejects_atomic(rejects, rej_path)
            stored = read_table(table, warehouse)  # hash what was actually written
            state[table] = {
                "fingerprint": fingerprint,
                "data_hash": data_hash(stored),
                "source_rows": len(legacy[table]),
                "migrated_rows": len(stored),
                "merged_rows": int((rejects["reason"] == "merged_duplicate").sum()),
                "rejected_rows": int((rejects["reason"] != "merged_duplicate").sum()),
            }
            _write_json_atomic(state_path, state)  # only now is the stage "done"
            s = state[table]
            print(f"  {table:<16} migrated {s['migrated_rows']:>9,}  merged {s['merged_rows']:>6,}"
                  f"  rejected {s['rejected_rows']:>6,}")
            summary[table] = {**state[table], "action": "migrated"}
            parents.pop(table, None)  # its data may have changed
        if crash_after == table:
            raise SimulatedCrash(f"simulated crash after stage {table!r}")
    return summary


def _parent_info(table: str, warehouse: Path, rejects_dir: Path):
    """(accepted keys, rejected keys as strings, extra) for FK checks against ``table``."""
    df = read_table(table, warehouse)
    pk = TABLES[table]["pk"][0]
    rejected = set(read_rejects(rejects_dir / f"{table}.csv")["key"])
    extra = dict(zip(df["set_num"], df["decade"].astype(int))) if table == "sets" else None
    return set(df[pk].tolist()), rejected, extra
