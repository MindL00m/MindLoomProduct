// Company Brain — Neo4j constraints & indexes for the knowledge graph.
// Run once against the target database before ingesting. Safe to re-run.

// --- Person -----------------------------------------------------------------
// Identity keys. person_id is the stable internal id; canonical_email is the
// de-duplication key for directory-sourced people (lower-cased email).
CREATE CONSTRAINT person_id_unique IF NOT EXISTS
FOR (p:Person) REQUIRE p.person_id IS UNIQUE;

CREATE CONSTRAINT person_canonical_email_unique IF NOT EXISTS
FOR (p:Person) REQUIRE p.canonical_email IS UNIQUE;

// canonical_name is NO LONGER a unique identity: real directories contain
// people who share a name, and people are now de-duplicated on email. Drop the
// old uniqueness constraint (if present) and keep canonical_name only as a
// lookup index. Chat-derived people (no email) still MERGE on canonical_name.
DROP CONSTRAINT person_canonical_name_unique IF EXISTS;

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

CREATE CONSTRAINT entity_canonical_name_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE e.canonical_name IS UNIQUE;

// --- Chunk ------------------------------------------------------------------
CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE;

// --- Question ---------------------------------------------------------------
CREATE CONSTRAINT question_id_unique IF NOT EXISTS
FOR (q:Question) REQUIRE q.question_id IS UNIQUE;

// --- Document ---------------------------------------------------------------
// document_id is the stable internal id; content_hash (sha256 of the raw bytes)
// is the de-duplication key so re-uploading identical content reuses one node.
CREATE CONSTRAINT document_id_unique IF NOT EXISTS
FOR (d:Document) REQUIRE d.document_id IS UNIQUE;

CREATE CONSTRAINT document_content_hash_unique IF NOT EXISTS
FOR (d:Document) REQUIRE d.content_hash IS UNIQUE;

CREATE INDEX document_source_index IF NOT EXISTS
FOR (d:Document) ON (d.source);

CREATE INDEX document_status_index IF NOT EXISTS
FOR (d:Document) ON (d.status);
