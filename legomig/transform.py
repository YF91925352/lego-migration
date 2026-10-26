"""Pure migration logic: legacy rows (all strings) -> typed rows + rejected rows with reasons.

Rules are applied in a fixed order and each row keeps the *first* reason it fails on:

1. type conversion        -> ``invalid_value:<legacy field>``
2. range checks           -> ``out_of_range:<column>``
3. foreign keys           -> ``parent_rejected:<table>`` (parent existed but was rejected)
                             ``fk_missing:<column>-><table>`` (parent never existed)
4. duplicate inventory lines (same set, part, colour, spare flag) are additive:
                             merged into the first line, quantities summed -> ``merged_duplicate``
   primary-key duplicates -> ``duplicate_identical`` (exact copy: first one kept)
                             ``duplicate_conflict`` (same key, different content: all rejected,
                             because there is no safe way to choose)

Nothing is dropped silently: every legacy row ends up either migrated, merged or rejected,
and merged/rejected rows are written out with their reason and source location.
"""
import json
from pathlib import Path

import pandas as pd

from .schema import TABLES

REJECT_COLUMNS = ["table", "key", "reason", "source_ref", "record"]


# ---------- reading the legacy export ----------------------------------------

def load_legacy(legacy_dir: Path) -> dict[str, pd.DataFrame]:
    """Flatten the legacy export into one all-string DataFrame per target table.
    ``_ref`` points back to the exact place in the legacy files."""
    catalog = json.loads((legacy_dir / "catalog.json").read_text(encoding="utf-8"))
    out = {}
    for table, section in [("colors", "colours"), ("themes", "themes"),
                           ("part_categories", "categories"), ("parts", "parts")]:
        rows = catalog[section]
        df = pd.DataFrame(rows, dtype=str).fillna("") if rows else pd.DataFrame()
        df["_ref"] = [f"catalog.json#{section}[{i}]" for i in range(len(rows))]
        out[table] = df

    sets, lines = [], []
    with open(legacy_dir / "sets.jsonl", encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            rec = json.loads(raw)
            inv = rec.pop("Inventory", []) or []
            sets.append({**{k: _s(v) for k, v in rec.items()}, "_ref": f"sets.jsonl:L{n}"})
            for j, item in enumerate(inv):
                lines.append({"SetNo": _s(rec.get("SetNo")), **{k: _s(v) for k, v in item.items()},
                              "_ref": f"sets.jsonl:L{n}#Inventory[{j}]"})
    out["sets"] = pd.DataFrame(sets)
    out["set_parts"] = pd.DataFrame(lines)
    return out


def _s(v) -> str:
    return "" if v is None else str(v)


# ---------- type conversion ---------------------------------------------------

def convert(s: pd.Series, kind: str) -> tuple[pd.Series, pd.Series]:
    """Return (typed values, mask of rows that could not be converted)."""
    s = s.astype(str).str.strip()
    if kind == "str":
        return s, s.eq("")
    if kind in ("int", "int_optional"):
        ok = s.str.fullmatch(r"-?\d+").astype(bool)
        bad = ~ok & ~s.eq("") if kind == "int_optional" else ~ok
        return pd.to_numeric(s.where(ok), errors="coerce").astype("Int64"), bad
    if kind == "yn_bool":
        val = s.map({"Y": True, "N": False}).astype("boolean")
        return val, val.isna()
    if kind == "hex_rgb":
        ok = s.str.fullmatch(r"#?[0-9A-Fa-f]{6}").astype(bool)
        return s.str.lstrip("#").str.upper().where(ok), ~ok
    raise ValueError(f"unknown type {kind!r} in mapping")


def _key(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    k = df[cols[0]].astype(str)
    for c in cols[1:]:
        k = k + "|" + df[c].astype(str)
    return k


# ---------- the migration of one table ---------------------------------------

def transform(table: str, raw: pd.DataFrame, mapping: dict, parents: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``parents[t] = (accepted_keys, rejected_keys_as_str, extra)`` for every parent table."""
    spec, fields = TABLES[table], mapping[table]["fields"]
    raw = raw.reset_index(drop=True)
    for src in fields:
        if src not in raw:
            raw[src] = ""
    to_src = {v["to"]: k for k, v in fields.items()}

    typed = pd.DataFrame(index=raw.index)
    reason = pd.Series(None, index=raw.index, dtype=object)

    # 1. types
    for src, f in fields.items():
        val, bad = convert(raw[src], f["type"])
        typed[f["to"]] = val
        reason = reason.mask(reason.isna() & bad, f"invalid_value:{src}")

    # 2. ranges
    for col, lo, hi in spec["checks"]:
        v = typed[col]
        bad = reason.isna() & ((v < lo) | (v > hi)).fillna(False).astype(bool)
        reason = reason.mask(bad, f"out_of_range:{col}")

    # 3. foreign keys to other tables
    for col, ptable, pcol in spec["fks"]:
        if ptable == table:
            continue
        accepted, rejected, _ = parents[ptable]
        v = typed[col]
        live = reason.isna() & v.notna()
        missing = live & ~v.isin(list(accepted))
        was_rejected = v.astype(str).isin(list(rejected))
        reason = reason.mask(missing & was_rejected, f"parent_rejected:{ptable}")
        reason = reason.mask(missing & ~was_rejected, f"fk_missing:{col}->{ptable}")

    # 3b. self-references (theme -> parent theme): repeat until nothing changes
    for col, ptable, pcol in spec["fks"]:
        if ptable != table:
            continue
        all_ids = set(typed[pcol].dropna())
        while True:
            alive = set(typed.loc[reason.isna(), pcol].dropna())
            v = typed[col]
            bad = reason.isna() & v.notna() & ~v.isin(alive)
            if not bad.any():
                break
            exists = v.isin(all_ids)
            reason = reason.mask(bad & exists, f"parent_rejected:{ptable}")
            reason = reason.mask(bad & ~exists, f"fk_missing:{col}->{ptable}")

    pk = spec["pk"]
    k = _key(typed, pk)

    # 4a. additive lines (set_parts): merge duplicates by summing, keep the first line
    if spec.get("merge_sum"):
        col = spec["merge_sum"]
        live = reason.isna()
        dup = live & live.groupby(k).transform("sum").gt(1)
        if dup.any():
            totals = typed.loc[dup].groupby(k[dup])[col].sum()
            first = ~k[dup].duplicated(keep="first")
            keep_idx = first[first].index
            typed.loc[keep_idx, col] = k[keep_idx].map(totals).astype("Int64")
            reason.loc[first[~first].index] = "merged_duplicate"

    # 4b. primary-key duplicates among rows that are still alive
    live = reason.isna()
    dup = live & live.groupby(k).transform("sum").gt(1)
    if dup.any():
        content_cols = [f["to"] for f in fields.values()]
        g = pd.DataFrame({"k": k[dup], "sig": _key(typed.loc[dup], content_cols)})
        variants = g.groupby("k")["sig"].nunique()
        conflict = g["k"].isin(variants[variants > 1].index)
        reason.loc[g.index[conflict]] = "duplicate_conflict"
        same = g[~conflict]
        reason.loc[same.index[same.duplicated("k", keep="first")]] = "duplicate_identical"

    # ---- split -----------------------------------------------------------------
    ok = reason.isna()
    accepted = typed[ok].copy()
    if table == "sets":
        accepted["decade"] = (accepted["year"] // 10 * 10).astype("Int64")
    if table == "set_parts":
        decade_by_set = parents["sets"][2]
        accepted["decade"] = accepted["set_num"].map(decade_by_set).astype("Int64")

    src_fields = list(fields)
    rej_idx = reason.index[~ok]
    stripped = raw.loc[rej_idx, src_fields].apply(lambda c: c.astype(str).str.strip())
    rejects = pd.DataFrame({
        "table": table,
        "key": _key(stripped, [to_src[c] for c in pk]),
        "reason": reason.loc[rej_idx],
        "source_ref": raw.loc[rej_idx, "_ref"],
        "record": [json.dumps(r, ensure_ascii=False) for r in raw.loc[rej_idx, src_fields].to_dict("records")],
    }, columns=REJECT_COLUMNS)
    return accepted.reset_index(drop=True), rejects.reset_index(drop=True)
