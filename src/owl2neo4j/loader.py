"""Neo4j loader for parsed OWL ontology graphs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, TypeVar

from neo4j import GraphDatabase

from .model import Neo4jProperty, OntologyGraph, OwlEntity, OwlRelationship


RESOURCE_LABELS = {
    "Ontology": "OwlOntology",
    "Class": "OwlClass",
    "ObjectProperty": "OwlObjectProperty",
    "DataProperty": "OwlDataProperty",
    "AnnotationProperty": "OwlAnnotationProperty",
    "Property": "OwlProperty",
    "Datatype": "OwlDatatype",
    "Individual": "OwlIndividual",
    "Restriction": "OwlRestriction",
    "ClassExpression": "OwlClassExpression",
    "BlankNode": "OwlBlankNode",
    "Resource": "OwlResourceNode",
}


@dataclass(frozen=True)
class LoadStats:
    resources: int
    literals: int
    relationships: int
    import_id: str


class Neo4jOntologyLoader:
    """Load an :class:`OntologyGraph` into Neo4j."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        *,
        database: str | None = None,
        batch_size: int = 1_000,
        driver=None,
    ) -> None:
        self.database = database
        self.batch_size = batch_size
        self._owns_driver = driver is None
        if driver is not None:
            self.driver = driver
        else:
            if not uri or user is None or password is None:
                raise ValueError("uri, user, and password are required when driver is not supplied")
            self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        if self._owns_driver:
            self.driver.close()

    def __enter__(self) -> "Neo4jOntologyLoader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def load(
        self,
        graph: OntologyGraph,
        *,
        import_id: str | None = None,
        clear_existing: bool = False,
        create_constraints: bool = True,
    ) -> LoadStats:
        import_id = import_id or graph_import_id(graph)

        with self.driver.session(database=self.database) as session:
            if create_constraints:
                self._create_constraints(session)
            if clear_existing:
                session.run(
                    "MATCH (n) WHERE n.import_id = $import_id DETACH DELETE n",
                    import_id=import_id,
                )

            self._load_resources(session, graph.entities.values(), import_id)
            self._load_literals(session, graph, import_id)
            self._apply_kind_labels(session, import_id)
            self._load_relationships(session, graph.relationships, import_id)

        return LoadStats(
            resources=len(graph.entities),
            literals=len(graph.literals),
            relationships=len(graph.relationships),
            import_id=import_id,
        )

    def _create_constraints(self, session) -> None:
        statements = [
            "CREATE CONSTRAINT owl2neo4j_resource_key IF NOT EXISTS "
            "FOR (n:OwlResource) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT owl2neo4j_literal_key IF NOT EXISTS "
            "FOR (n:OwlLiteral) REQUIRE n.key IS UNIQUE",
            "CREATE INDEX owl2neo4j_resource_iri IF NOT EXISTS "
            "FOR (n:OwlResource) ON (n.iri)",
            "CREATE INDEX owl2neo4j_resource_kind IF NOT EXISTS "
            "FOR (n:OwlResource) ON (n.kind)",
        ]
        for statement in statements:
            session.run(statement)

    def _load_resources(self, session, entities: Iterable[OwlEntity], import_id: str) -> None:
        rows = [
            {"key": entity.key, "props": clean_properties({**entity.as_properties(), "import_id": import_id})}
            for entity in entities
        ]
        for batch in batched(rows, self.batch_size):
            session.run(
                """
                UNWIND $rows AS row
                MERGE (n:OwlResource {key: row.key})
                SET n += row.props
                """,
                rows=batch,
            )

    def _load_literals(self, session, graph: OntologyGraph, import_id: str) -> None:
        rows = [
            {"key": literal.key, "props": clean_properties({**literal.as_properties(), "import_id": import_id})}
            for literal in graph.literals.values()
        ]
        for batch in batched(rows, self.batch_size):
            session.run(
                """
                UNWIND $rows AS row
                MERGE (n:OwlLiteral {key: row.key})
                SET n += row.props
                """,
                rows=batch,
            )

    def _apply_kind_labels(self, session, import_id: str) -> None:
        for kind, label in RESOURCE_LABELS.items():
            session.run(
                f"""
                MATCH (n:OwlResource {{import_id: $import_id, kind: $kind}})
                SET n:{safe_label(label)}
                """,
                import_id=import_id,
                kind=kind,
            )

    def _load_relationships(
        self,
        session,
        relationships: Iterable[OwlRelationship],
        import_id: str,
    ) -> None:
        entity_rows_by_type: dict[str, list[dict[str, object]]] = {}
        literal_rows_by_type: dict[str, list[dict[str, object]]] = {}

        for relationship in relationships:
            props = clean_properties(
                {
                    **relationship.properties,
                    "key": relationship_key(relationship, import_id),
                    "import_id": import_id,
                }
            )
            row = {
                "key": props["key"],
                "start_key": relationship.start_key,
                "end_key": relationship.end_key,
                "props": props,
            }
            target = literal_rows_by_type if relationship.end_is_literal else entity_rows_by_type
            target.setdefault(relationship.rel_type, []).append(row)

        for rel_type, rows in entity_rows_by_type.items():
            self._run_relationship_batches(session, rel_type, rows, literal_target=False)
        for rel_type, rows in literal_rows_by_type.items():
            self._run_relationship_batches(session, rel_type, rows, literal_target=True)

    def _run_relationship_batches(
        self,
        session,
        rel_type: str,
        rows: list[dict[str, object]],
        *,
        literal_target: bool,
    ) -> None:
        relationship_type = safe_relationship_type(rel_type)
        end_label = "OwlLiteral" if literal_target else "OwlResource"
        for batch in batched(rows, self.batch_size):
            session.run(
                f"""
                UNWIND $rows AS row
                MATCH (s:OwlResource {{key: row.start_key}})
                MATCH (o:{end_label} {{key: row.end_key}})
                MERGE (s)-[r:{relationship_type} {{key: row.key}}]->(o)
                SET r += row.props
                """,
                rows=batch,
            )


def graph_import_id(graph: OntologyGraph) -> str:
    payload = {
        "source": graph.source,
        "ontology_iri": graph.ontology_iri,
        "triple_count": graph.triple_count,
    }
    return "owl:" + stable_hash(payload)[:24]


def relationship_key(relationship: OwlRelationship, import_id: str) -> str:
    payload = {
        "import_id": import_id,
        "start_key": relationship.start_key,
        "end_key": relationship.end_key,
        "rel_type": relationship.rel_type,
        "end_is_literal": relationship.end_is_literal,
        "properties": relationship.properties,
    }
    return "rel:" + stable_hash(payload)


def clean_properties(properties: dict[str, object]) -> dict[str, Neo4jProperty]:
    cleaned: dict[str, Neo4jProperty] = {}
    for key, value in properties.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        elif isinstance(value, (set, tuple)):
            cleaned[key] = [str(item) for item in value]
        elif isinstance(value, list):
            cleaned[key] = [item if isinstance(item, (str, int, float, bool)) else str(item) for item in value]
        else:
            cleaned[key] = str(value)
    return cleaned


def safe_relationship_type(rel_type: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", rel_type):
        raise ValueError(f"Unsafe Neo4j relationship type: {rel_type!r}")
    return f"`{rel_type}`"


def safe_label(label: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", label):
        raise ValueError(f"Unsafe Neo4j label: {label!r}")
    return f"`{label}`"


T = TypeVar("T")


def batched(items: Iterable[T], size: int) -> Iterator[list[T]]:
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def stable_hash(value: object) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
