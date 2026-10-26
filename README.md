# lego-migration

[![CI](https://github.com/YF91925352/lego-migration/actions/workflows/ci.yml/badge.svg)](https://github.com/YF91925352/lego-migration/actions/workflows/ci.yml)

A small, complete data migration: move a **legacy JSON export** of the LEGO catalogue into **typed, partitioned Parquet**, and **prove that no data was lost**.

The source is the public [Rebrickable](https://rebrickable.com/downloads/) LEGO database: sets, parts, colours, themes and which parts go into which set. The CSV dumps are turned into a legacy-style export that plays the role of the old system, and that export is migrated to the new format.

```
Rebrickable CSVs ──► legacy export (JSON) ──► migrate ──► Parquet warehouse ──► DuckDB SQL
   (source of truth)   "the old system"         │            typed, partitioned
                                                ├──► rejects/<table>.csv  (every row not migrated, with reason)
                                                └──► state.json           (what has been done, for re-runs)
                                       verify ◄─┘  independent checks ──► reports/migration_report.md
```

## Run it

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m legomig all     # download → export → migrate → verify → query
pytest -q                 # tests run on small invented fixture data, no download needed
```

Each step can also be run on its own: `python -m legomig download | export | migrate | verify | query`.

## 1. The legacy format

`legomig/legacy_export.py` writes what an older system typically exports:

| | Legacy export | Target |
|---|---|---|
| Types | everything is a string: `"Yr": "1999"` | `year int16`, `quantity int32`, … |
| Booleans | `"Y"` / `"N"` | `bool` |
| Colours | `"#C91A09"` | `"C91A09"` |
| Names | `SetNo`, `ColourRef`, `PieceCount` | `set_num`, `color_id`, `num_parts` |
| Shape | each set's inventory **nested** in the set record (JSON Lines) | flat `set_parts` table |

The field mapping lives in [`config/mapping.json`](config/mapping.json), separate from the code. Scope decisions for the export (only the latest inventory version per set, minifigure inventories left out) are counted and reported, so even those rows are accounted for.

To give the checks something real to catch, the export injects a few **seeded, documented defects**: duplicate records, a set re-entered with a different name, orphan references, a year typo, a zero quantity and an invalid flag. They are listed in `_injected_defects.json`, and the report shows how many were caught. Run with `--defects 0` for a clean export.

## 2. Migration to Parquet

- **Explicit schema** per table in [`legomig/schema.py`](legomig/schema.py): column types, primary key, foreign keys and range checks.
- **Partitioning**: `sets` and `set_parts` are partitioned by `decade` (Hive style, `decade=1990/`), the natural filter for catalogue questions. The small dimension tables are single files.
- Tables are migrated parents first: colours, themes and part categories, then parts, sets and set inventories.

## 3. Validation and reconciliation

Every legacy row ends up in exactly one place: **migrated**, **merged** or **rejected**. Merged and rejected rows go to `out/rejects/<table>.csv` with the reason, the exact location in the legacy files (`sets.jsonl:L812#Inventory[3]`) and the original record.

| Rule (applied in this order) | Reason recorded |
|---|---|
| Value can't be converted to the target type | `invalid_value:<field>` |
| Value outside its allowed range (year 1949–2100, quantity ≥ 1) | `out_of_range:<column>` |
| Referenced parent exists but was itself rejected | `parent_rejected:<table>` |
| Referenced parent never existed | `fk_missing:<column>-><table>` |
| Same part and colour listed twice in one inventory | `merged_duplicate` (quantities summed) |
| Exact duplicate record | `duplicate_identical` (first one kept) |
| Same key, different content | `duplicate_conflict` (all copies rejected: there is no safe way to choose) |

Verification (`legomig/report.py`) is **independent of the migration code**: it re-reads the legacy files with plain `json` and queries the Parquet output with DuckDB SQL. It checks:

- **Row counts**: legacy = migrated + merged + rejected, for every table
- **Primary keys** are unique, and **foreign keys** have zero orphans (SQL anti-joins)
- **Column types** in every Parquet file match the target schema
- **Aggregates**: the total number of pieces is conserved, and the piece total of every migrated set matches the source
- Every set number is accounted for, and **every injected defect was caught** with the expected reason

A test deletes one row from the reject file and confirms that verification then **fails**. A check that can't fail proves nothing.

## 4. Re-running safely

- **Idempotent**: each table stage has a fingerprint made of the schema version, the legacy files, the mapping and the data hashes of its parent tables. A second run skips every stage.
- **Deterministic**: output is sorted by primary key, and a content hash (independent of Parquet file metadata) shows that a run from scratch produces identical data.
- **Resumable**: outputs are written to a temp folder and swapped into place, and `state.json` is updated only after that. `--crash-after parts` simulates a crash, and the next run redoes only the unfinished tables.

## 5. SQL on the result

[`queries/`](queries) holds DuckDB SQL that runs directly on the Parquet files: sets per decade, top themes rolled up through the theme tree (recursive CTE), colour share of all pieces (window function), declared versus counted piece numbers, and a summary of the reject CSVs. The output is in [`reports/query_results.md`](reports/query_results.md).

```sql
SELECT c.name AS colour, sum(p.quantity)::BIGINT AS pieces,
       round(100.0 * sum(p.quantity) / sum(sum(p.quantity)) OVER (), 1) AS pct
FROM set_parts p JOIN colors c USING (color_id)
WHERE NOT p.is_spare
GROUP BY c.name ORDER BY pieces DESC LIMIT 10;
```

## 6. Tests and CI

`pytest` covers the conversion and validation rules, the legacy export, Parquet types and partitions, the reconciliation, idempotency, determinism, resuming after a crash, change detection, the SQL queries, and a negative test where verification must fail. GitHub Actions runs the suite on Python 3.11 and 3.12 on every push, then runs the whole pipeline on the fixture data twice, checking that the second run skips everything.

The test fixtures are **invented rows** in Rebrickable's column format, so no third-party data is stored in the repository.

## 7. Migration report

Filled in automatically by `python -m legomig all`. The numbers below are from a run on the full Rebrickable dataset downloaded on 26 October 2026, with the default 3 injected defects per type. The full version is in [`reports/migration_report.md`](reports/migration_report.md).

<!-- REPORT:START -->
**Result: PASS** (28/28 checks passed)

#### Rows per table

| table | legacy rows | migrated | merged | rejected | legacy = migrated + merged + rejected |
|---|---:|---:|---:|---:|:---:|
| colors | 275 | 275 | 0 | 0 | ✅ |
| themes | 496 | 496 | 0 | 0 | ✅ |
| part_categories | 76 | 76 | 0 | 0 | ✅ |
| parts | 64,909 | 64,903 | 0 | 6 | ✅ |
| sets | 28,556 | 28,547 | 0 | 9 | ✅ |
| set_parts | 1,358,350 | 1,358,134 | 0 | 216 | ✅ |

#### Merged and rejected rows by reason

Every row is in `out/rejects/<table>.csv` with its reason, its exact location in the legacy files and the original record.

| table | reason | rows |
|---|---|---:|
| parts | `duplicate_identical` | 3 |
| parts | `fk_missing:part_cat_id->part_categories` | 3 |
| set_parts | `parent_rejected:sets` | 204 |
| set_parts | `fk_missing:color_id->colors` | 3 |
| set_parts | `invalid_value:Spare` | 3 |
| set_parts | `out_of_range:quantity` | 3 |
| set_parts | `parent_rejected:parts` | 3 |
| sets | `duplicate_conflict` | 6 |
| sets | `invalid_value:Yr` | 3 |

#### Checks

| | check | detail |
|:---:|---|---|
| ✅ | rows reconcile: colors | 275 = 275 migrated + 0 merged + 0 rejected |
| ✅ | rows reconcile: themes | 496 = 496 migrated + 0 merged + 0 rejected |
| ✅ | rows reconcile: part_categories | 76 = 76 migrated + 0 merged + 0 rejected |
| ✅ | rows reconcile: parts | 64,909 = 64,903 migrated + 0 merged + 6 rejected |
| ✅ | rows reconcile: sets | 28,556 = 28,547 migrated + 0 merged + 9 rejected |
| ✅ | rows reconcile: set_parts | 1,358,350 = 1,358,134 migrated + 0 merged + 216 rejected |
| ✅ | primary key unique: colors (color_id) | 0 duplicates |
| ✅ | primary key unique: themes (theme_id) | 0 duplicates |
| ✅ | primary key unique: part_categories (part_cat_id) | 0 duplicates |
| ✅ | primary key unique: parts (part_num) | 0 duplicates |
| ✅ | primary key unique: sets (set_num) | 0 duplicates |
| ✅ | primary key unique: set_parts (set_num, part_num, color_id, is_spare) | 0 duplicates |
| ✅ | foreign key: themes.parent_id -> themes.theme_id | 0 orphans |
| ✅ | foreign key: parts.part_cat_id -> part_categories.part_cat_id | 0 orphans |
| ✅ | foreign key: sets.theme_id -> themes.theme_id | 0 orphans |
| ✅ | foreign key: set_parts.set_num -> sets.set_num | 0 orphans |
| ✅ | foreign key: set_parts.part_num -> parts.part_num | 0 orphans |
| ✅ | foreign key: set_parts.color_id -> colors.color_id | 0 orphans |
| ✅ | column types: colors | all match |
| ✅ | column types: themes | all match |
| ✅ | column types: part_categories | all match |
| ✅ | column types: parts | all match |
| ✅ | column types: sets | all match |
| ✅ | column types: set_parts | all match |
| ✅ | piece total conserved | 4,703,096 = 4,702,638 migrated + 458 rejected |
| ✅ | per-set piece totals match | 28,547 sets compared, 0 mismatches |
| ✅ | every set number accounted for | 28,553 distinct, 0 unaccounted |
| ✅ | injected defects caught | 21/21 caught |

#### Injected defects

Seeded defects added to the legacy export to prove the checks catch real problems:

- `duplicate_conflict`: 3
- `duplicate_identical`: 3
- `invalid_boolean`: 3
- `invalid_year`: 3
- `orphan_category`: 3
- `orphan_colour`: 3
- `zero_quantity`: 3

#### Scope of the legacy export (CSV → legacy JSON)

- Inventories: 28,553 latest versions used, 1,953 older versions and 17,314 non-set inventories (minifigs) out of scope
- Inventory lines: 1,358,287 exported, 212,035 belong to out-of-scope inventories (total 1,570,322 in CSV)
- Sets without any inventory: 8,636
<!-- REPORT:END -->

## What the migrated data showed

The point of a migration is data people can use, so here are a few things the SQL on the new Parquet tables surfaced. Full output is in [`reports/query_results.md`](reports/query_results.md).

- **`num_parts` should not be trusted blindly.** For 10,849 sets the declared piece count matches the migrated inventory exactly, and for 3,366 it is within 5%. For 5,681 sets it differs by more than 5%, probably because minifigure parts live in separate inventories. Another 8,651 sets have no (non-spare) inventory at all.
- **Scope is explicit.** 212,035 of the 1,570,322 inventory lines in the CSV belong to older inventory versions or minifigure inventories. They are deliberately left out of the legacy export, and the report counts them instead of letting them disappear.
- **Three colours make up 40% of all pieces:** Black 18.0%, Light Bluish Gray 11.4% and White 11.1%.
- **Size:** the migrated warehouse is 7.6 MB of Parquet. For comparison, the inventory CSV alone is 129 MB, although it also carries image URLs that were not migrated.

## Optional: AI-assisted field mapping

[`scripts/suggest_mapping.py`](scripts/suggest_mapping.py) shows a model (Claude) the legacy field names, a few sample values and the target schema, and asks for a mapping with a confidence score and a short reason per field. The suggestion is saved separately and compared line by line with the human-confirmed `config/mapping.json` (agree / differs / missing). The pipeline only ever reads the confirmed mapping: the model speeds up the first draft, and a person decides.

```bash
python scripts/suggest_mapping.py --dry-run           # show the prompt
ANTHROPIC_API_KEY=... python scripts/suggest_mapping.py
```

## Design choices and trade-offs

- **pandas plus pyarrow for the transform, DuckDB for verification.** Verifying with a different engine than the one that wrote the data catches more bugs. The full dataset (1.36 million inventory lines) runs end to end in a few minutes on a laptop; in a stress test, the migration step for 1.3 million synthetic lines took under 30 seconds.
- **Reject, don't fix.** The migration never guesses, for example which of two conflicting set records is right. It sets the row aside with a reason, so a person can decide.
- **Merge additive duplicates.** Two inventory lines for the same part and colour are not an error; their quantities add up. The current Rebrickable dump has none, but the rule keeps the migration correct for exports that do. It is unit-tested and counted in the report like everything else.
- **Next steps at a larger scale:** stream the legacy file in chunks instead of loading it whole, write rejects to a table as well, and run each stage as a task in an orchestrator (for example Airflow or Dagster), reusing the same fingerprint logic.

## Data and licence

**Data source: [Rebrickable](https://rebrickable.com/downloads/).** Rebrickable's catalogue downloads are free for personal and commercial use, provided Rebrickable is acknowledged as the data source. The data is downloaded at run time and is not redistributed in this repository. The download step only fetches files that are not already present, well within Rebrickable's limit of one automated download per day, and it never scrapes web pages.

LEGO® is a trademark of the LEGO Group, which does not sponsor or endorse this project.
