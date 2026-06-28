// Company Brain — Neo4j uniqueness constraints for the knowledge graph.
// Run once against the target database before ingesting.

// Person: stable id plus a canonical_name used as the de-duplication key.
CREATE CONSTRAINT person_id_unique IF NOT EXISTS
FOR (p:Person) REQUIRE p.person_id IS UNIQUE;

CREATE CONSTRAINT person_canonical_name_unique IF NOT EXISTS
FOR (p:Person) REQUIRE p.canonical_name IS UNIQUE;

// Entity: stable id plus a canonical_name used as the de-duplication key.
CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE;

CREATE CONSTRAINT entity_canonical_name_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE e.canonical_name IS UNIQUE;

// Chunk.
CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE;

// Question.
CREATE CONSTRAINT question_id_unique IF NOT EXISTS
FOR (q:Question) REQUIRE q.question_id IS UNIQUE;
