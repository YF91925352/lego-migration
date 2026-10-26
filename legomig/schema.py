"""Target model: the contract the new system expects.

Types are given as short names and turned into Arrow types in ``storage.py`` so the
rest of the code (and the tests of pure logic) does not depend on pyarrow.
"""

# Bump when the target schema or a migration rule changes: every stage re-runs.
SCHEMA_VERSION = "1"

# Order matters: parents are migrated before the tables that reference them.
ORDER = ["colors", "themes", "part_categories", "parts", "sets", "set_parts"]

TABLES = {
    "colors": {
        "columns": {"color_id": "int32", "name": "string", "rgb": "string", "is_trans": "bool"},
        "pk": ["color_id"],
        "fks": [],
        "checks": [],
        "partition": None,
    },
    "themes": {
        "columns": {"theme_id": "int32", "name": "string", "parent_id": "int32"},
        "pk": ["theme_id"],
        "fks": [("parent_id", "themes", "theme_id")],  # self-reference, nullable
        "checks": [],
        "partition": None,
    },
    "part_categories": {
        "columns": {"part_cat_id": "int32", "name": "string"},
        "pk": ["part_cat_id"],
        "fks": [],
        "checks": [],
        "partition": None,
    },
    "parts": {
        "columns": {"part_num": "string", "name": "string", "part_cat_id": "int32"},
        "pk": ["part_num"],
        "fks": [("part_cat_id", "part_categories", "part_cat_id")],
        "checks": [],
        "partition": None,
    },
    "sets": {
        "columns": {
            "set_num": "string", "name": "string", "year": "int16",
            "theme_id": "int32", "num_parts": "int32", "decade": "int16",
        },
        "pk": ["set_num"],
        "fks": [("theme_id", "themes", "theme_id")],
        # (column, min, max) — inclusive range checks
        "checks": [("year", 1949, 2100), ("num_parts", 0, 1_000_000)],
        "partition": "decade",
    },
    "set_parts": {
        "columns": {
            "set_num": "string", "part_num": "string", "color_id": "int32",
            "quantity": "int32", "is_spare": "bool", "decade": "int16",
        },
        "pk": ["set_num", "part_num", "color_id", "is_spare"],
        "fks": [
            ("set_num", "sets", "set_num"),
            ("part_num", "parts", "part_num"),
            ("color_id", "colors", "color_id"),
        ],
        "checks": [("quantity", 1, 100_000)],
        # The same part in the same colour can appear on two lines of one inventory.
        # That is not an error: the quantities add up. Merge instead of rejecting.
        "merge_sum": "quantity",
        "partition": "decade",
    },
}
