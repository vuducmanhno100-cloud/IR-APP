WITH RECURSIVE up AS (
    SELECT p.id, NULL::bigint AS edge_id, NULL::bigint AS child_id, NULL::text AS level, 0 AS depth, ARRAY[p.id] AS path
    FROM process_instances p
    WHERE p.id = %(process_id)s
    UNION ALL
    SELECT e.parent_id, e.id, up.id, e.level, up.depth - 1, up.path || e.parent_id
    FROM up
    JOIN process_edges e ON e.child_id = up.id AND e.level = ANY(%(levels)s)
    WHERE up.depth > -%(max_up)s
      AND NOT e.parent_id = ANY(up.path)
)
SELECT DISTINCT ON (id) id, edge_id, child_id, level, depth
FROM up
ORDER BY id, depth DESC
