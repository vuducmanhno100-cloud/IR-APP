SELECT e.parent_id AS id, count(*) AS children
FROM process_edges e
WHERE e.parent_id = ANY(%(ids)s)
  AND e.level = ANY(%(levels)s)
GROUP BY e.parent_id
