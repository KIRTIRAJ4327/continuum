-- Neo4j schema for Continuum
-- Run this once against a fresh DB:
--   cat graph_db/schema.cypher | cypher-shell -u neo4j -p continuum-dev

-- ── Core DAG nodes ───────────────────────────────────────────────────────────
CREATE CONSTRAINT feature_id   IF NOT EXISTS FOR (f:Feature)   REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT story_id     IF NOT EXISTS FOR (s:Story)     REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT task_id      IF NOT EXISTS FOR (t:Task)      REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT component_id IF NOT EXISTS FOR (c:Component) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT artifact_id  IF NOT EXISTS FOR (a:Artifact)  REQUIRE a.id IS UNIQUE;

-- ── M3: Episodic memory ───────────────────────────────────────────────────────
-- Episode: one agent decision + its outcome recorded after each run.
CREATE CONSTRAINT episode_id IF NOT EXISTS FOR (e:Episode) REQUIRE e.id IS UNIQUE;

-- ADR (Architecture Decision Record): durable standards the pipeline must honour.
CREATE CONSTRAINT adr_id IF NOT EXISTS FOR (a:ADR) REQUIRE a.id IS UNIQUE;

-- Entity: any named artifact touched during a run (file, table, endpoint, …).
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE;

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX feature_created   IF NOT EXISTS FOR (f:Feature) ON (f.created_at);
CREATE INDEX task_status       IF NOT EXISTS FOR (t:Task)    ON (t.status);
CREATE INDEX story_state       IF NOT EXISTS FOR (s:Story)   ON (s.state);
CREATE INDEX episode_agent     IF NOT EXISTS FOR (e:Episode) ON (e.agent);
CREATE INDEX episode_timestamp IF NOT EXISTS FOR (e:Episode) ON (e.timestamp);
CREATE INDEX episode_feature   IF NOT EXISTS FOR (e:Episode) ON (e.feature_id);

-- ── Full-text index (GraphRAG search) ─────────────────────────────────────────
-- Used by `graphrag_query` skill: CALL db.index.fulltext.queryNodes(...)
CREATE FULLTEXT INDEX episode_search IF NOT EXISTS
    FOR (e:Episode)
    ON EACH [e.decision, e.action, e.outcome, e.request_text];

-- ── Relationship types (documentation — not enforced by Cypher) ───────────────
-- (Feature)-[:HAS_TASK]->(Task)
-- (Feature)-[:HAS_EPISODE]->(Episode)
-- (Episode)-[:ABOUT]->(Story)
-- (Episode)-[:CAUSED]->(Entity)       -- file/table/endpoint the episode touched
-- (Episode)-[:GOVERNED_BY]->(ADR)     -- which ADR the decision followed
-- (Task)-[:DEPENDS_ON]->(Task)

-- ── Example: seed a root ADR ─────────────────────────────────────────────────
-- MERGE (a:ADR {id: 'ADR-001'})
-- ON CREATE SET a.title = 'Schema First', a.rule = 'DATABASE runs before BACKEND',
--              a.rationale = 'Migrations must exist before the API code that uses them',
--              a.created_at = datetime();
