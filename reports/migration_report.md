# Migration report

**Result: PASS** (28/28 checks passed)

## Rows per table

| table | legacy rows | migrated | merged | rejected | legacy = migrated + merged + rejected |
|---|---:|---:|---:|---:|:---:|
| colors | 275 | 275 | 0 | 0 | ✅ |
| themes | 496 | 496 | 0 | 0 | ✅ |
| part_categories | 76 | 76 | 0 | 0 | ✅ |
| parts | 64,909 | 64,903 | 0 | 6 | ✅ |
| sets | 28,556 | 28,547 | 0 | 9 | ✅ |
| set_parts | 1,358,350 | 1,358,134 | 0 | 216 | ✅ |

## Merged and rejected rows by reason

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

## Checks

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

## Injected defects

Seeded defects added to the legacy export to prove the checks catch real problems:

- `duplicate_conflict`: 3
- `duplicate_identical`: 3
- `invalid_boolean`: 3
- `invalid_year`: 3
- `orphan_category`: 3
- `orphan_colour`: 3
- `zero_quantity`: 3

## Scope of the legacy export (CSV → legacy JSON)

- Inventories: 28,553 latest versions used, 1,953 older versions and 17,314 non-set inventories (minifigs) out of scope
- Inventory lines: 1,358,287 exported, 212,035 belong to out-of-scope inventories (total 1,570,322 in CSV)
- Sets without any inventory: 8,636
