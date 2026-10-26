"""Unit tests for the migration rules (pandas only, no files)."""
import json

import pandas as pd

from conftest import MAPPING
from legomig.transform import convert, transform

MAP = json.loads(MAPPING.read_text())


def raw(rows):
    df = pd.DataFrame(rows, dtype=str)
    df["_ref"] = [f"test[{i}]" for i in range(len(rows))]
    return df


def test_convert_int_rejects_garbage_but_keeps_negatives():
    val, bad = convert(pd.Series(["4", " 12 ", "-1", "19x9", ""]), "int")
    assert list(bad) == [False, False, False, True, True]
    assert list(val[:3]) == [4, 12, -1]


def test_convert_optional_int_allows_empty():
    val, bad = convert(pd.Series(["", "7", "x"]), "int_optional")
    assert list(bad) == [False, False, True]
    assert pd.isna(val[0]) and val[1] == 7


def test_convert_yn_bool_and_rgb():
    val, bad = convert(pd.Series(["Y", "N", "?"]), "yn_bool")
    assert list(bad) == [False, False, True]
    assert bool(val[0]) is True and bool(val[1]) is False
    val, bad = convert(pd.Series(["#c91a09", "FFFFFF", "#12"]), "hex_rgb")
    assert list(bad) == [False, False, True] and val[0] == "C91A09"


def test_identical_duplicate_keeps_first_and_rejects_copy():
    df = raw([{"CatID": "11", "CatName": "Bricks"}, {"CatID": "11", "CatName": "Bricks"}])
    ok, rej = transform("part_categories", df, MAP, {})
    assert len(ok) == 1
    assert rej[["key", "reason", "source_ref"]].values.tolist() == [["11", "duplicate_identical", "test[1]"]]


def test_conflicting_duplicate_rejects_all_copies():
    df = raw([{"CatID": "11", "CatName": "Bricks"}, {"CatID": "11", "CatName": "Blocks"}])
    ok, rej = transform("part_categories", df, MAP, {})
    assert len(ok) == 0
    assert set(rej["reason"]) == {"duplicate_conflict"} and len(rej) == 2


def test_foreign_key_distinguishes_missing_from_rejected_parent():
    df = raw([
        {"PartNo": "a", "PartDesc": "A", "CategoryRef": "11"},   # ok
        {"PartNo": "b", "PartDesc": "B", "CategoryRef": "14"},   # parent was rejected
        {"PartNo": "c", "PartDesc": "C", "CategoryRef": "99"},   # parent never existed
    ])
    parents = {"part_categories": ({11}, {"14"}, None)}
    ok, rej = transform("parts", df, MAP, parents)
    assert ok["part_num"].tolist() == ["a"]
    assert dict(zip(rej["key"], rej["reason"])) == {
        "b": "parent_rejected:part_categories",
        "c": "fk_missing:part_cat_id->part_categories",
    }


def test_self_reference_cascades_through_theme_tree():
    df = raw([
        {"ThemeID": "1", "ThemeName": "Root", "ParentTheme": ""},
        {"ThemeID": "2", "ThemeName": "Child of missing", "ParentTheme": "9"},
        {"ThemeID": "3", "ThemeName": "Grandchild", "ParentTheme": "2"},
    ])
    ok, rej = transform("themes", df, MAP, {})
    assert ok["theme_id"].tolist() == [1]
    assert dict(zip(rej["key"], rej["reason"])) == {"2": "fk_missing:parent_id->themes", "3": "parent_rejected:themes"}


def test_first_failing_rule_wins_and_record_is_kept_verbatim():
    df = raw([{"SetNo": "1-1", "SetName": "X", "Yr": "19x9", "ThemeRef": "404", "PieceCount": "3"}])
    ok, rej = transform("sets", df, MAP, {"themes": ({1}, set(), None)})
    assert len(ok) == 0
    assert rej.loc[0, "reason"] == "invalid_value:Yr"  # type error is reported before the FK problem
    assert json.loads(rej.loc[0, "record"])["Yr"] == "19x9"


def test_duplicate_inventory_lines_are_merged_not_rejected():
    df = raw([
        {"SetNo": "1-1", "PartNo": "a", "ColourRef": "4", "Qty": "5", "Spare": "N"},
        {"SetNo": "1-1", "PartNo": "a", "ColourRef": "4", "Qty": "2", "Spare": "N"},
        {"SetNo": "1-1", "PartNo": "a", "ColourRef": "4", "Qty": "1", "Spare": "Y"},  # spare: separate line
    ])
    parents = {"sets": ({"1-1"}, set(), {"1-1": 1990}), "parts": ({"a"}, set(), None), "colors": ({4}, set(), None)}
    ok, rej = transform("set_parts", df, MAP, parents)
    assert sorted(zip(ok["is_spare"], ok["quantity"])) == [(False, 7), (True, 1)]
    assert rej[["reason", "source_ref"]].values.tolist() == [["merged_duplicate", "test[1]"]]
