WITH RECURSIVE down AS (
    SELECT p.id, NULL::bigint AS edge_id, NULL::bigint AS parent_id, NULL::text AS level, 0 AS depth, ARRAY[p.id] AS path
    FROM process_instances p
    WHERE p.id = %(process_id)s
    UNION ALL
    SELECT e.child_id, e.id, down.id, e.level, down.depth + 1, down.path || e.child_id
    FROM down
    JOIN process_edges e ON e.parent_id = down.id AND e.level = ANY(%(levels)s)
    WHERE down.depth < %(max_depth)s
      AND NOT e.child_id = ANY(down.path)
)
SELECT id, edge_id, parent_id, level, depth
FROM down
LIMIT %(max_nodes)s
