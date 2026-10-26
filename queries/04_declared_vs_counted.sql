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
