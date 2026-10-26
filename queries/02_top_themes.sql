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
