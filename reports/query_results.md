# Query results

Produced by `python -m legomig query`: DuckDB reading the migrated Parquet files directly.

## 01_sets_per_decade

```sql
-- How many sets were released per decade, and how big were they on average?
-- Reads only the partitions it needs (decade is a Hive partition column).
SELECT decade,
       count(*)                AS sets,
       round(avg(num_parts))   AS avg_parts_declared
FROM sets
GROUP BY decade
ORDER BY decade;
```

| decade | sets | avg_parts_declared |
|---|---|---|
| 1940 | 5 | 97.0 |
| 1950 | 175 | 17.0 |
| 1960 | 445 | 69.0 |
| 1970 | 662 | 104.0 |
| 1980 | 1,272 | 88.0 |
| 1990 | 2,221 | 119.0 |
| 2000 | 4,842 | 137.0 |
| 2010 | 9,287 | 147.0 |
| 2020 | 9,638 | 227.0 |

## 02_top_themes

```sql
-- Themes with the most sets, rolled up to their top-level parent theme.
WITH RECURSIVE lineage AS (
    SELECT theme_id, theme_id AS root_id FROM themes WHERE parent_id IS NULL
    UNION ALL
    SELECT t.theme_id, l.root_id FROM themes t JOIN lineage l ON t.parent_id = l.theme_id
)
SELECT r.name                    AS top_level_theme,
       count(*)                  AS sets,
       concat(min(s.year), '–', max(s.year)) AS years
FROM sets s
JOIN lineage l ON l.theme_id = s.theme_id
JOIN themes r  ON r.theme_id = l.root_id
GROUP BY r.name
ORDER BY sets DESC
LIMIT 10;
```

| top_level_theme | sets | years |
|---|---|---|
| Gear | 6,541 | 1966–2027 |
| Books | 1,480 | 1955–2027 |
| Duplo | 1,464 | 1969–2026 |
| Educational and Dacta | 1,128 | 1960–2026 |
| Star Wars | 1,103 | 1999–2026 |
| City | 1,020 | 2005–2026 |
| Collectible Minifigures | 1,005 | 2008–2026 |
| Friends | 683 | 2012–2026 |
| Ninjago | 680 | 2011–2027 |
| Town | 679 | 1978–2008 |

## 03_most_used_colors

```sql
-- Which colours account for most pieces (spare parts excluded)?
SELECT c.name                                   AS colour,
       '#' || c.rgb                             AS rgb,
       sum(p.quantity)::BIGINT                  AS pieces,
       round(100.0 * sum(p.quantity) / sum(sum(p.quantity)) OVER (), 1) AS pct
FROM set_parts p
JOIN colors c USING (color_id)
WHERE NOT p.is_spare
GROUP BY c.name, c.rgb
ORDER BY pieces DESC
LIMIT 10;
```

| colour | rgb | pieces | pct |
|---|---|---|---|
| Black | #05131D | 824,010 | 18.0 |
| Light Bluish Gray | #A0A5A9 | 522,915 | 11.4 |
| White | #FFFFFF | 508,784 | 11.1 |
| Dark Bluish Gray | #6C6E68 | 374,237 | 8.2 |
| Red | #C91A09 | 315,470 | 6.9 |
| Yellow | #F2CD37 | 202,831 | 4.4 |
| Blue | #0055BF | 202,523 | 4.4 |
| Tan | #E4CD9E | 198,404 | 4.3 |
| Reddish Brown | #582A12 | 179,767 | 3.9 |
| Green | #237841 | 91,105 | 2.0 |

## 04_declared_vs_counted

```sql
-- Data-quality probe: does the declared piece count match the inventory we migrated?
-- (Differences are expected — e.g. minifigure parts live in separate inventories —
--  but the size of the gap is worth knowing before anyone relies on num_parts.)
WITH counted AS (
    SELECT set_num, sum(quantity) FILTER (WHERE NOT is_spare) AS counted
    FROM set_parts GROUP BY set_num
)
SELECT CASE
         WHEN c.counted IS NULL            THEN 'no inventory'
         WHEN c.counted = s.num_parts      THEN 'exact match'
         WHEN abs(c.counted - s.num_parts) <= 0.05 * s.num_parts THEN 'within 5%'
         ELSE 'differs by more than 5%'
       END        AS comparison,
       count(*)   AS sets
FROM sets s LEFT JOIN counted c USING (set_num)
GROUP BY 1
ORDER BY sets DESC;
```

| comparison | sets |
|---|---|
| exact match | 10,849 |
| no inventory | 8,651 |
| differs by more than 5% | 5,681 |
| within 5% | 3,366 |

## 05_rejects_summary

```sql
-- The reject files are CSV; DuckDB queries them alongside the Parquet tables.
SELECT "table", reason, count(*) AS rows, min(source_ref) AS example_location
FROM rejects
GROUP BY "table", reason
ORDER BY "table", rows DESC;
```

| table | reason | rows | example_location |
|---|---|---|---|
| parts | duplicate_identical | 3 | catalog.json#parts[64906] |
| parts | fk_missing:part_cat_id->part_categories | 3 | catalog.json#parts[16051] |
| set_parts | parent_rejected:sets | 204 | sets.jsonl:L18794#Inventory[0] |
| set_parts | out_of_range:quantity | 3 | sets.jsonl:L1193#Inventory[0] |
| set_parts | parent_rejected:parts | 3 | sets.jsonl:L15858#Inventory[27] |
| set_parts | invalid_value:Spare | 3 | sets.jsonl:L1083#Inventory[0] |
| set_parts | fk_missing:color_id->colors | 3 | sets.jsonl:L1263#Inventory[0] |
| sets | duplicate_conflict | 6 | sets.jsonl:L27327 |
| sets | invalid_value:Yr | 3 | sets.jsonl:L18794 |
