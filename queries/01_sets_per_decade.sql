-- How many sets were released per decade, and how big were they on average?
-- Reads only the partitions it needs (decade is a Hive partition column).
SELECT decade,
       count(*)                AS sets,
       round(avg(num_parts))   AS avg_parts_declared
FROM sets
GROUP BY decade
ORDER BY decade;
