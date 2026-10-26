"""Simulate the *old system*: turn the Rebrickable CSVs into a legacy JSON export.

The legacy format deliberately looks like something a real older system produces:

* everything is a string (``"Yr": "1999"``, ``"Qty": "4"``)
* booleans are ``"Y"``/``"N"``, colours are ``"#RRGGBB"``
* legacy field names (``SetNo``, ``ColourRef``, ``PieceCount``…)
* set inventories are **nested** inside each set record (one JSON object per line)

Optionally a small, seeded, fully documented set of defects is injected
(duplicates, orphan references, invalid values) so the validation step has
something real to catch. Every injected defect is listed in
``_injected_defects.json`` so we can measure whether the migration caught all of them.
"""
import json
import random
from pathlib import Path

import pandas as pd

TRUE = {"t", "true", "1", "y", "yes"}
FALSE = {"f", "false", "0", "n", "no"}


def _read(raw_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(raw_dir / f"{name}.csv", dtype=str, keep_default_na=False)


def _yn(v: str) -> str:
    s = v.strip().lower()
    return "Y" if s in TRUE else "N" if s in FALSE else v


def build_legacy(raw_dir: Path):
    """Return (catalog, set_records, stats) built from the CSV dumps, without defects."""
    colors, themes = _read(raw_dir, "colors"), _read(raw_dir, "themes")
    cats, parts, sets = _read(raw_dir, "part_categories"), _read(raw_dir, "parts"), _read(raw_dir, "sets")
    inventories, inv_parts = _read(raw_dir, "inventories"), _read(raw_dir, "inventory_parts")

    catalog = {
        "system": "BrickBase 2.3 export",
        "colours": [
            {"ColourID": r.id, "ColourName": r.name, "RGB": "#" + r.rgb, "Transparent": _yn(r.is_trans)}
            for r in colors.itertuples()
        ],
        "themes": [
            {"ThemeID": r.id, "ThemeName": r.name, "ParentTheme": r.parent_id} for r in themes.itertuples()
        ],
        "categories": [{"CatID": r.id, "CatName": r.name} for r in cats.itertuples()],
        "parts": [
            {"PartNo": r.part_num, "PartDesc": r.name, "CategoryRef": r.part_cat_id} for r in parts.itertuples()
        ],
    }

    # The legacy system kept one inventory per set: the latest version.
    set_nums = set(sets["set_num"])
    inventories = inventories.assign(_v=pd.to_numeric(inventories["version"]))
    is_set = inventories["set_num"].isin(set_nums)
    latest = inventories[is_set].sort_values(["set_num", "_v"]).groupby("set_num").tail(1)
    inv_to_set = dict(zip(latest["id"], latest["set_num"]))

    used = inv_parts["inventory_id"].isin(inv_to_set.keys())
    lines: dict[str, list] = {}
    for r in inv_parts[used].itertuples():
        lines.setdefault(inv_to_set[r.inventory_id], []).append(
            {"PartNo": r.part_num, "ColourRef": r.color_id, "Qty": r.quantity, "Spare": _yn(r.is_spare)}
        )

    set_records = [
        {
            "SetNo": r.set_num, "SetName": r.name, "Yr": r.year, "ThemeRef": r.theme_id,
            "PieceCount": r.num_parts, "Inventory": lines.get(r.set_num, []),
        }
        for r in sets.sort_values("set_num").itertuples()
    ]

    stats = {
        "csv_rows": {
            "colors": len(colors), "themes": len(themes), "part_categories": len(cats), "parts": len(parts),
            "sets": len(sets), "inventories": len(inventories), "inventory_parts": len(inv_parts),
        },
        "inventories_used_latest_version": len(latest),
        "inventories_skipped_older_version": int(is_set.sum()) - len(latest),
        "inventories_skipped_not_a_set": int((~is_set).sum()),
        "inventory_parts_exported": int(used.sum()),
        "inventory_parts_skipped_with_their_inventory": int((~used).sum()),
        "sets_without_inventory": sum(1 for s in set_records if not s["Inventory"]),
    }
    return catalog, set_records, stats


def inject_defects(catalog: dict, set_records: list, per_type: int, seed: int) -> list[dict]:
    """Corrupt a few records in place. Returns a list describing each defect and the
    reason the migration is expected to give when it rejects it."""
    rng = random.Random(seed)
    defects = []

    def note(kind, table, key, reason):
        defects.append({"type": kind, "table": table, "key": key, "expected_reason": reason})

    # --- catalogue-level defects ------------------------------------------------
    for p in rng.sample(catalog["parts"], min(per_type, len(catalog["parts"]))):
        catalog["parts"].append(dict(p))  # exact duplicate (e.g. exported twice)
        note("duplicate_identical", "parts", p["PartNo"], "duplicate_identical")
    pool = [p for p in catalog["parts"] if not any(d["key"] == p["PartNo"] for d in defects)]
    for p in rng.sample(pool, min(per_type, len(pool))):
        p["CategoryRef"] = "99999"  # points at a category that does not exist
        note("orphan_category", "parts", p["PartNo"], "fk_missing:part_cat_id->part_categories")

    # --- set-level and inventory-line defects (each on a different set) ---------
    # Defects must not overlap, otherwise one hides the other: inventory-line defects
    # go on a line whose part is untouched.
    bad_parts = {d["key"] for d in defects}
    clean_line = {}
    for s in set_records:
        idx = next((i for i, x in enumerate(s["Inventory"]) if x["PartNo"] not in bad_parts), None)
        if idx is not None:
            clean_line[s["SetNo"]] = idx
    with_inv = [s for s in set_records if s["SetNo"] in clean_line]
    victims = rng.sample(with_inv, min(len(with_inv), per_type * 5))
    groups = [victims[i::5] for i in range(5)]
    pick = lambda s: s["Inventory"][clean_line[s["SetNo"]]]  # noqa: E731

    for s in groups[0]:
        copy = {**s, "SetName": s["SetName"] + " (re-entered)", "Inventory": [dict(x) for x in s["Inventory"]]}
        set_records.append(copy)  # same key, different content: nobody can tell which is right
        note("duplicate_conflict", "sets", s["SetNo"], "duplicate_conflict")
    for s in groups[1]:
        s["Yr"] = s["Yr"][:2] + "x" + s["Yr"][3:]  # typo in a numeric field
        note("invalid_year", "sets", s["SetNo"], "invalid_value:Yr")
    for s in groups[2]:
        line = pick(s)
        line["ColourRef"] = "99999"
        note("orphan_colour", "set_parts", "|".join([s["SetNo"], line["PartNo"], "99999", line["Spare"]]),
             "fk_missing:color_id->colors")
    for s in groups[3]:
        line = pick(s)
        line["Qty"] = "0"
        note("zero_quantity", "set_parts", "|".join([s["SetNo"], line["PartNo"], line["ColourRef"], line["Spare"]]),
             "out_of_range:quantity")
    for s in groups[4]:
        line = pick(s)
        line["Spare"] = "?"
        note("invalid_boolean", "set_parts", "|".join([s["SetNo"], line["PartNo"], line["ColourRef"], "?"]),
             "invalid_value:Spare")

    set_records.sort(key=lambda s: s["SetNo"])
    return defects


def export(raw_dir: Path, legacy_dir: Path, defects_per_type: int = 0, seed: int = 42) -> dict:
    catalog, set_records, stats = build_legacy(raw_dir)
    defects = inject_defects(catalog, set_records, defects_per_type, seed) if defects_per_type else []

    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(legacy_dir / "sets.jsonl", "w", encoding="utf-8") as f:
        for s in set_records:
            f.write(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n")
    (legacy_dir / "_injected_defects.json").write_text(json.dumps(defects, indent=1), encoding="utf-8")
    (legacy_dir / "_export_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return {"stats": stats, "defects": len(defects)}
