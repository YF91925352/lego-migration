"""End to end: Parquet output, verification, idempotency, resume, SQL."""
import json
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import ROOT, export_and_migrate
from legomig.migrate import SimulatedCrash
from legomig.query import run_queries
from legomig.report import verify


def data_hashes(workdir):
    state = json.loads((workdir / "out" / "state.json").read_text())
    return {t: s["data_hash"] for t, s in state.items()}


def test_clean_migration_moves_every_row_and_passes_all_checks(workdir):
    export_and_migrate(workdir)
    r = verify(workdir / "out" / "legacy", workdir / "out")
    assert r["ok"], [c for c in r["checks"] if not c["ok"]]
    counts = {t["table"]: (t["source"], t["migrated"], t["merged"], t["rejected"]) for t in r["tables"]}
    assert counts["sets"] == (11, 11, 0, 0)
    # set 101-1 lists the same part/colour twice: one line merged into the other, nothing rejected
    assert counts["set_parts"] == (22, 21, 1, 0)
    assert r["reject_reasons"] == [{"table": "set_parts", "reason": "merged_duplicate", "rows": 1}]


def test_dirty_migration_rejects_with_reasons_and_still_reconciles(workdir):
    export_and_migrate(workdir, defects=1)
    r = verify(workdir / "out" / "legacy", workdir / "out")
    assert r["ok"], [c for c in r["checks"] if not c["ok"]]
    assert r["missed_defects"] == []
    for t in r["tables"]:
        assert t["source"] == t["migrated"] + t["merged"] + t["rejected"]
    reasons = {x["reason"] for x in r["reject_reasons"]}
    assert {"duplicate_conflict", "duplicate_identical", "parent_rejected:sets",
            "fk_missing:color_id->colors", "out_of_range:quantity"} <= reasons


def test_parquet_has_explicit_types_and_decade_partitions(workdir):
    export_and_migrate(workdir)
    wh = workdir / "out" / "warehouse"
    schema = pq.read_schema(next((wh / "set_parts").rglob("*.parquet")))
    assert schema.field("color_id").type == pa.int32()
    assert schema.field("quantity").type == pa.int32()
    assert schema.field("is_spare").type == pa.bool_()
    assert pq.read_schema(next((wh / "sets").rglob("*.parquet"))).field("year").type == pa.int16()
    assert sorted(p.name for p in (wh / "sets").iterdir()) == [
        "decade=1970", "decade=1980", "decade=1990", "decade=2000", "decade=2010", "decade=2020"]


def test_rerun_is_a_no_op(workdir):
    export_and_migrate(workdir, defects=1)
    first = data_hashes(workdir)
    second_run = export_and_migrate(workdir, defects=1)
    assert {s["action"] for s in second_run.values()} == {"skipped"}
    assert data_hashes(workdir) == first


def test_same_input_gives_same_data_from_scratch(workdir):
    export_and_migrate(workdir, defects=1)
    first = data_hashes(workdir)
    shutil.rmtree(workdir / "out" / "warehouse")
    shutil.rmtree(workdir / "out" / "rejects")
    (workdir / "out" / "state.json").unlink()
    export_and_migrate(workdir, defects=1)
    assert data_hashes(workdir) == first


def test_resumes_after_a_crash(workdir):
    with pytest.raises(SimulatedCrash):
        export_and_migrate(workdir, defects=1, crash_after="parts")
    state = json.loads((workdir / "out" / "state.json").read_text())
    assert set(state) == {"colors", "themes", "part_categories", "parts"}

    resumed = export_and_migrate(workdir, defects=1)
    assert [t for t, s in resumed.items() if s["action"] == "skipped"] == ["colors", "themes", "part_categories", "parts"]
    assert [t for t, s in resumed.items() if s["action"] == "migrated"] == ["sets", "set_parts"]
    assert verify(workdir / "out" / "legacy", workdir / "out")["ok"]


def test_changed_source_triggers_a_rerun(workdir):
    export_and_migrate(workdir)
    sets = workdir / "out" / "legacy" / "sets.jsonl"
    sets.write_text(sets.read_text().replace('"Little Pier"', '"Little Pier II"'))
    again = export_and_migrate(workdir)
    assert again["sets"]["action"] == "migrated"


def test_sql_queries_run_on_the_parquet_output(workdir):
    export_and_migrate(workdir, defects=1)
    md = run_queries(workdir / "out", ROOT / "queries")
    for name in ["01_sets_per_decade", "02_top_themes", "03_most_used_colors",
                 "04_declared_vs_counted", "05_rejects_summary"]:
        assert f"## {name}" in md
    assert "| Townscape |" in md  # Harbour and Rail roll up to their parent theme


def test_verification_fails_when_a_row_goes_missing(workdir):
    """The checks are only worth something if they can fail."""
    export_and_migrate(workdir, defects=1)
    rejects = workdir / "out" / "rejects" / "set_parts.csv"
    lines = rejects.read_text().splitlines()
    rejects.write_text("\n".join(lines[:-1]) + "\n")  # silently lose one rejected line
    r = verify(workdir / "out" / "legacy", workdir / "out")
    assert not r["ok"]
    assert [c["check"] for c in r["checks"] if not c["ok"]][0] == "rows reconcile: set_parts"
