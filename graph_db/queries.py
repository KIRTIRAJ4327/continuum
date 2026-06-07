"""Reusable Cypher queries."""

GET_UNBLOCKED_TASKS = """
MATCH (t:Task {status: 'pending'})
WHERE NONE(d IN [(t)-[:DEPENDS_ON]->(dep) | dep] WHERE d.status <> 'done')
RETURN t
"""

WRITE_DAG = """
UNWIND $tasks AS task_data
CREATE (t:Task {
    id: task_data.id,
    title: task_data.title,
    status: 'pending',
    created_at: datetime()
})
WITH t, task_data
UNWIND task_data.depends_on AS dep_id
MATCH (dep:Task {id: dep_id})
CREATE (t)-[:DEPENDS_ON]->(dep)
"""

UPDATE_TASK_STATUS = """
MATCH (t:Task {id: $task_id})
SET t.status = $status
RETURN t
"""
