// Loom — Neo4j constraints & indexes for the multi-tenant knowledge graph.
// Run once against the target database before ingesting. Safe to re-run.

// --- Person -----------------------------------------------------------------
CREATE CONSTRAINT person_id_unique IF NOT EXISTS
FOR (p:Person) REQUIRE p.person_id IS UNIQUE;

// Per-org email de-duplication (directory-sourced people).
DROP CONSTRAINT person_canonical_email_unique IF EXISTS;
DROP CONSTRAINT person_org_email_unique IF EXISTS;
CREATE CONSTRAINT person_org_email_unique IF NOT EXISTS
FOR (p:Person) REQUIRE (p.org_id, p.canonical_email) IS UNIQUE;

// Chat-derived people (no email) merge on (org_id, canonical_name).
DROP CONSTRAINT person_canonical_name_unique IF EXISTS;

CREATE INDEX person_org_id_index IF NOT EXISTS
FOR (p:Person) ON (p.org_id);

CREATE INDEX person_canonical_name_index IF NOT EXISTS
FOR (p:Person) ON (p.canonical_name);

CREATE INDEX person_email_index IF NOT EXISTS
FOR (p:Person) ON (p.email);

CREATE INDEX person_department_index IF NOT EXISTS
FOR (p:Person) ON (p.department);

CREATE INDEX person_status_index IF NOT EXISTS
FOR (p:Person) ON (p.status);

// --- Entity -----------------------------------------------------------------
CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE;

DROP CONSTRAINT entity_canonical_name_unique IF EXISTS;
// Community Edition: composite UNIQUE (NODE KEY is Enterprise-only).
CREATE CONSTRAINT entity_org_name_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE (e.org_id, e.canonical_name) IS UNIQUE;

CREATE INDEX entity_org_id_index IF NOT EXISTS
FOR (e:Entity) ON (e.org_id);

// --- Chunk ------------------------------------------------------------------
CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE;

CREATE INDEX chunk_org_id_index IF NOT EXISTS
FOR (c:Chunk) ON (c.org_id);

// --- Question ---------------------------------------------------------------
CREATE CONSTRAINT question_id_unique IF NOT EXISTS
FOR (q:Question) REQUIRE q.question_id IS UNIQUE;

CREATE INDEX question_org_id_index IF NOT EXISTS
FOR (q:Question) ON (q.org_id);

// --- Document ---------------------------------------------------------------
CREATE CONSTRAINT document_id_unique IF NOT EXISTS
FOR (d:Document) REQUIRE d.document_id IS UNIQUE;

DROP CONSTRAINT document_content_hash_unique IF EXISTS;
// Community Edition: composite UNIQUE (NODE KEY is Enterprise-only).
CREATE CONSTRAINT document_org_hash_unique IF NOT EXISTS
FOR (d:Document) REQUIRE (d.org_id, d.content_hash) IS UNIQUE;

CREATE INDEX document_org_id_index IF NOT EXISTS
FOR (d:Document) ON (d.org_id);

CREATE INDEX document_source_index IF NOT EXISTS
FOR (d:Document) ON (d.source);

CREATE INDEX document_status_index IF NOT EXISTS
FOR (d:Document) ON (d.status);

// --- Extracted knowledge ----------------------------------------------------
CREATE CONSTRAINT decision_id_unique IF NOT EXISTS
FOR (d:Decision) REQUIRE d.decision_id IS UNIQUE;
CREATE CONSTRAINT action_item_id_unique IF NOT EXISTS
FOR (a:ActionItem) REQUIRE a.action_item_id IS UNIQUE;
CREATE CONSTRAINT claim_id_unique IF NOT EXISTS
FOR (c:Claim) REQUIRE c.claim_id IS UNIQUE;
CREATE CONSTRAINT open_issue_id_unique IF NOT EXISTS
FOR (i:OpenIssue) REQUIRE i.issue_id IS UNIQUE;
CREATE INDEX decision_org_id_index IF NOT EXISTS
FOR (d:Decision) ON (d.org_id);
CREATE INDEX action_item_org_id_index IF NOT EXISTS
FOR (a:ActionItem) ON (a.org_id);
CREATE INDEX claim_org_id_index IF NOT EXISTS
FOR (c:Claim) ON (c.org_id);
CREATE INDEX open_issue_org_id_index IF NOT EXISTS
FOR (i:OpenIssue) ON (i.org_id);
CREATE INDEX open_issue_status_index IF NOT EXISTS
FOR (i:OpenIssue) ON (i.status);
CREATE INDEX action_item_status_index IF NOT EXISTS
FOR (a:ActionItem) ON (a.status);
CREATE INDEX entity_type_index IF NOT EXISTS
FOR (e:Entity) ON (e.type);
CREATE INDEX entity_work_status_index IF NOT EXISTS
FOR (e:Entity) ON (e.work_status);

// Lookup indexes for cross-chunk lifecycle updates (merged in app on org_id+canonical_key).
CREATE INDEX action_item_canonical_key_index IF NOT EXISTS
FOR (a:ActionItem) ON (a.canonical_key);
CREATE INDEX open_issue_canonical_key_index IF NOT EXISTS
FOR (i:OpenIssue) ON (i.canonical_key);
