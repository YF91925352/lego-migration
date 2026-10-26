"""The simulated legacy export looks like an old system's output."""
import json

from legomig.legacy_export import export


def test_legacy_format_is_stringly_typed_and_nested(workdir):
    legacy = workdir / "legacy"
    export(workdir / "raw", legacy)
    catalog = json.loads((legacy / "catalog.json").read_text())
    assert catalog["colours"][-1] == {"ColourID": "47", "ColourName": "Trans-Clear", "RGB": "#FCFCFC", "Transparent": "Y"}
    sets = [json.loads(line) for line in (legacy / "sets.jsonl").read_text().splitlines()]
    pier = next(s for s in sets if s["SetNo"] == "100-1")
    assert pier["Yr"] == "1978" and pier["Inventory"][2] == {"PartNo": "p1x1", "ColourRef": "47", "Qty": "2", "Spare": "Y"}


def test_latest_inventory_version_only_and_scope_is_accounted_for(workdir):
    legacy = workdir / "legacy"
    res = export(workdir / "raw", legacy)
    sets = {s["SetNo"]: s for s in map(json.loads, (legacy / "sets.jsonl").read_text().splitlines())}
    assert len(sets["103-1"]["Inventory"]) == 3  # version 2, not version 1
    assert sets["110-1"]["Inventory"] == []
    s = res["stats"]
    # every CSV inventory line is either exported or explicitly out of scope
    assert s["inventory_parts_exported"] + s["inventory_parts_skipped_with_their_inventory"] == s["csv_rows"]["inventory_parts"]


def test_defect_injection_is_seeded_and_documented(workdir):
    a, b = workdir / "a", workdir / "b"
    export(workdir / "raw", a, defects_per_type=1, seed=7)
    export(workdir / "raw", b, defects_per_type=1, seed=7)
    assert (a / "sets.jsonl").read_bytes() == (b / "sets.jsonl").read_bytes()
    defects = json.loads((a / "_injected_defects.json").read_text())
    assert {d["type"] for d in defects} == {
        "duplicate_identical", "orphan_category", "duplicate_conflict",
        "invalid_year", "orphan_colour", "zero_quantity", "invalid_boolean",
    }
