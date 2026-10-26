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
