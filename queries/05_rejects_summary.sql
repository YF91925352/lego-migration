-- The reject files are CSV; DuckDB queries them alongside the Parquet tables.
SELECT "table", reason, count(*) AS rows, min(source_ref) AS example_location
FROM rejects
GROUP BY "table", reason
ORDER BY "table", rows DESC;
