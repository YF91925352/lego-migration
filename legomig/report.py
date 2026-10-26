"""Independent verification: prove that nothing was lost.

This module deliberately does **not** reuse the migration code. It re-reads the legacy
files with plain ``json`` and checks the Parquet output with DuckDB SQL, so a bug in
the migration cannot hide itself in the check.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from .schema import ORDER, TABLES
from .storage import arrow_schema


def _int(v):
    try:
        return int(str(v).strip())
    except ValueError:
        return None


def legacy_facts(legacy_dir: Path) -> dict:
    catalog = json.loads((legacy_dir / "catalog.json").read_text(encoding="utf-8"))
    counts = {"colors": len(catalog["colours"]), "themes": len(catalog["themes"]),
              "part_categories": len(catalog["categories"]), "parts": len(catalog["parts"]),
              "sets": 0, "set_parts": 0}
    set_nos, qty_total, qty_by_set = set(), 0, defaultdict(int)
    with open(legacy_dir / "sets.jsonl", encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            counts["sets"] += 1
            set_nos.add(str(s["SetNo"]).strip())
            for item in s.get("Inventory") or []:
                counts["set_parts"] += 1
                q = _int(item.get("Qty"))
                if q is not None:
                    qty_total += q
                    qty_by_set[str(s["SetNo"]).strip()] += q
    return {"counts": counts, "set_nos": set_nos, "qty_total": qty_total, "qty_by_set": qty_by_set}


def connect(out_dir: Path) -> duckdb.DuckDBPyConnection:
    """DuckDB views over the Parquet output and the reject files."""
    con = duckdb.connect()
    for t in ORDER:
        path = (out_dir / "warehouse" / t).as_posix()
        if TABLES[t]["partition"]:
            con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{path}/**/*.parquet', hive_partitioning = true)")
        else:
            con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{path}/*.parquet')")
    rej = (out_dir / "rejects").as_posix()
    cols = "{'table': 'VARCHAR', 'key': 'VARCHAR', 'reason': 'VARCHAR', 'source_ref': 'VARCHAR', 'record': 'VARCHAR'}"
    con.execute(f"CREATE VIEW rejects AS SELECT * FROM read_csv('{rej}/*.csv', header = true, delim = ',', "
                f"quote = '\"', escape = '\"', columns = {cols})")
    return con


def verify(legacy_dir: Path, out_dir: Path) -> dict:
    facts = legacy_facts(legacy_dir)
    con = connect(out_dir)
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1. Row reconciliation: source = migrated + rejected, per table
    tables = []
    rej_counts = dict(q("SELECT \"table\", count(*) FROM rejects WHERE reason <> 'merged_duplicate' GROUP BY 1"))
    merged_counts = dict(q("SELECT \"table\", count(*) FROM rejects WHERE reason = 'merged_duplicate' GROUP BY 1"))
    for t in ORDER:
        src = facts["counts"][t]
        mig = q(f"SELECT count(*) FROM {t}")[0][0]
        rej, mer = rej_counts.get(t, 0), merged_counts.get(t, 0)
        ok = src == mig + mer + rej
        tables.append({"table": t, "source": src, "migrated": mig, "merged": mer, "rejected": rej, "ok": ok})
        check(f"rows reconcile: {t}", ok, f"{src:,} = {mig:,} migrated + {mer:,} merged + {rej:,} rejected")

    # 2. Primary keys unique
    for t in ORDER:
        pk = ", ".join(TABLES[t]["pk"])
        d = q(f"SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT {pk} FROM {t})) FROM {t}")[0][0]
        check(f"primary key unique: {t} ({pk})", d == 0, f"{d} duplicates")

    # 3. Foreign keys: zero orphans
    for t in ORDER:
        for col, parent, pcol in TABLES[t]["fks"]:
            n = q(f"SELECT count(*) FROM {t} c WHERE c.{col} IS NOT NULL "
                  f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{pcol} = c.{col})")[0][0]
            check(f"foreign key: {t}.{col} -> {parent}.{pcol}", n == 0, f"{n} orphans")

    # 4. Types: every Parquet file carries exactly the target schema
    for t in ORDER:
        expected = arrow_schema(t)
        part = TABLES[t]["partition"]
        bad = []
        for f in sorted((out_dir / "warehouse" / t).rglob("*.parquet")):
            got = pq.read_schema(f)
            for field in expected:
                if field.name != part and (field.name not in got.names or got.field(field.name).type != field.type):
                    bad.append(f"{f.name}:{field.name}")
        check(f"column types: {t}", not bad, "all match" if not bad else ", ".join(bad[:5]))

    # 5. Aggregates: total quantity of pieces is conserved
    mig_qty = q("SELECT coalesce(sum(quantity), 0) FROM set_parts")[0][0]
    # merged lines are not subtracted: their quantity was added to the line they merged into
    rej_records = [json.loads(r[0]) for r in q(
        "SELECT record FROM rejects WHERE \"table\" = 'set_parts' AND reason <> 'merged_duplicate'")]
    rej_qty = sum(_int(r.get("Qty")) or 0 for r in rej_records)
    check("piece total conserved", facts["qty_total"] == mig_qty + rej_qty,
          f"{facts['qty_total']:,} = {mig_qty:,} migrated + {rej_qty:,} rejected")

    # 6. Per-set piece totals match for every migrated set
    rej_by_set = defaultdict(int)
    for r in rej_records:
        rej_by_set[str(r.get("SetNo", "")).strip()] += _int(r.get("Qty")) or 0
    mig_by_set = dict(q("SELECT s.set_num, coalesce(sum(p.quantity), 0) FROM sets s "
                        "LEFT JOIN set_parts p USING (set_num) GROUP BY 1"))
    mismatch = [s for s, n in mig_by_set.items() if facts["qty_by_set"].get(s, 0) - rej_by_set.get(s, 0) != n]
    check("per-set piece totals match", not mismatch, f"{len(mig_by_set):,} sets compared, {len(mismatch)} mismatches")

    # 7. Every distinct set number is accounted for
    mig_sets = {r[0] for r in q("SELECT set_num FROM sets")}
    rej_sets = {r[0] for r in q("SELECT key FROM rejects WHERE \"table\" = 'sets'")}
    lost = facts["set_nos"] - mig_sets - rej_sets
    check("every set number accounted for", not lost, f"{len(facts['set_nos']):,} distinct, {len(lost)} unaccounted")

    # 8. Every injected defect was caught with the expected reason
    defects_path = legacy_dir / "_injected_defects.json"
    defects = json.loads(defects_path.read_text()) if defects_path.exists() else []
    found = {(t, k, r) for t, k, r in q("SELECT \"table\", key, reason FROM rejects")}
    missed = [d for d in defects if (d["table"], d["key"], d["expected_reason"]) not in found]
    check("injected defects caught", not missed, f"{len(defects) - len(missed)}/{len(defects)} caught")

    reasons = q("SELECT \"table\", reason, count(*) FROM rejects GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2")
    stats_path = legacy_dir / "_export_stats.json"
    return {
        "tables": tables,
        "checks": checks,
        "reject_reasons": [{"table": t, "reason": r, "rows": n} for t, r, n in reasons],
        "injected_defects": dict(Counter(d["type"] for d in defects)),
        "missed_defects": missed,
        "export_stats": json.loads(stats_path.read_text()) if stats_path.exists() else None,
        "ok": all(c["ok"] for c in checks),
    }


def to_markdown(r: dict) -> str:
    L = ["# Migration report", "", f"**Result: {'PASS' if r['ok'] else 'FAIL'}** "
         f"({sum(c['ok'] for c in r['checks'])}/{len(r['checks'])} checks passed)", "",
         "## Rows per table", "",
         "| table | legacy rows | migrated | merged | rejected | legacy = migrated + merged + rejected |",
         "|---|---:|---:|---:|---:|:---:|"]
    for t in r["tables"]:
        L.append(f"| {t['table']} | {t['source']:,} | {t['migrated']:,} | {t['merged']:,} | {t['rejected']:,} "
                 f"| {'✅' if t['ok'] else '❌'} |")
    L += ["", "## Merged and rejected rows by reason", "",
          "Every row is in `out/rejects/<table>.csv` with its reason, its exact location in the legacy files "
          "and the original record.", "", "| table | reason | rows |", "|---|---|---:|"]
    L += [f"| {x['table']} | `{x['reason']}` | {x['rows']:,} |" for x in r["reject_reasons"]] or ["| – | – | 0 |"]
    L += ["", "## Checks", "", "| | check | detail |", "|:---:|---|---|"]
    L += [f"| {'✅' if c['ok'] else '❌'} | {c['check']} | {c['detail']} |" for c in r["checks"]]
    if r["injected_defects"]:
        L += ["", "## Injected defects", "",
              "Seeded defects added to the legacy export to prove the checks catch real problems:", ""]
        L += [f"- `{k}`: {v}" for k, v in sorted(r["injected_defects"].items())]
    if r["export_stats"]:
        s = r["export_stats"]
        L += ["", "## Scope of the legacy export (CSV → legacy JSON)", "",
              f"- Inventories: {s['inventories_used_latest_version']:,} latest versions used, "
              f"{s['inventories_skipped_older_version']:,} older versions and "
              f"{s['inventories_skipped_not_a_set']:,} non-set inventories (minifigs) out of scope",
              f"- Inventory lines: {s['inventory_parts_exported']:,} exported, "
              f"{s['inventory_parts_skipped_with_their_inventory']:,} belong to out-of-scope inventories "
              f"(total {s['csv_rows']['inventory_parts']:,} in CSV)",
              f"- Sets without any inventory: {s['sets_without_inventory']:,}"]
    return "\n".join(L) + "\n"
