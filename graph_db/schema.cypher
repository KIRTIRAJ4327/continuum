-- Neo4j schema for Continuum

CREATE CONSTRAINT feature_id IF NOT EXISTS FOR (f:Feature) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT story_id IF NOT EXISTS FOR (s:Story) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT task_id IF NOT EXISTS FOR (t:Task) REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT component_id IF NOT EXISTS FOR (c:Component) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT artifact_id IF NOT EXISTS FOR (a:Artifact) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT task_depends_on IF NOT EXISTS FOR ()-[r:DEPENDS_ON]-() REQUIRE r IS NOT NULL;

CREATE INDEX feature_created IF NOT EXISTS FOR (f:Feature) ON (f.created_at);
CREATE INDEX task_status IF NOT EXISTS FOR (t:Task) ON (t.status);
CREATE INDEX story_state IF NOT EXISTS FOR (s:Story) ON (s.state);

-- Get unblocked tasks:
-- MATCH (t:Task {status: 'pending'})
-- WHERE NONE(d IN [(t)-[:DEPENDS_ON]->(dep) | dep] WHERE d.status <> 'done')
-- RETURN t
