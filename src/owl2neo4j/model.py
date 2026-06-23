"""Data records produced by the OWL parser and consumed by the Neo4j loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonScalar = str | int | float | bool | None
Neo4jProperty = str | int | float | bool | list[str] | list[int] | list[float] | list[bool]


@dataclass(frozen=True)
class LiteralValue:
    """A literal object from an RDF/OWL assertion."""

    key: str
    value: str
    datatype: str | None = None
    language: str | None = None

    def as_properties(self) -> dict[str, Neo4jProperty]:
        props: dict[str, Neo4jProperty] = {
            "key": self.key,
            "value": self.value,
        }
        if self.datatype:
            props["datatype"] = self.datatype
        if self.language:
            props["language"] = self.language
        return props


@dataclass
class OwlEntity:
    """A node in the graph, normally an IRI resource or a blank node."""

    key: str
    iri: str | None
    kind: str
    labels: set[str] = field(default_factory=set)
    name: str | None = None
    namespace: str | None = None
    properties: dict[str, Neo4jProperty] = field(default_factory=dict)

    def as_properties(self) -> dict[str, Neo4jProperty]:
        props: dict[str, Neo4jProperty] = {
            "key": self.key,
            "kind": self.kind,
            **self.properties,
        }
        if self.iri:
            props["iri"] = self.iri
        if self.name:
            props["name"] = self.name
        if self.namespace:
            props["namespace"] = self.namespace
        if self.labels:
            props["owl_labels"] = sorted(self.labels)
        return props


@dataclass(frozen=True)
class OwlRelationship:
    """A relationship between two entity/literal records."""

    start_key: str
    end_key: str
    rel_type: str
    properties: dict[str, Neo4jProperty] = field(default_factory=dict)
    end_is_literal: bool = False

    def identity(self) -> tuple[str, str, str, tuple[tuple[str, str], ...], bool]:
        props = tuple(sorted((key, repr(value)) for key, value in self.properties.items()))
        return self.start_key, self.end_key, self.rel_type, props, self.end_is_literal


@dataclass
class OntologyGraph:
    """Complete parse result for an ontology."""

    source: str
    ontology_iri: str | None = None
    entities: dict[str, OwlEntity] = field(default_factory=dict)
    literals: dict[str, LiteralValue] = field(default_factory=dict)
    relationships: list[OwlRelationship] = field(default_factory=list)
    triple_count: int = 0

    def add_entity(self, entity: OwlEntity) -> OwlEntity:
        existing = self.entities.get(entity.key)
        if existing is None:
            self.entities[entity.key] = entity
            return entity

        existing.labels.update(entity.labels)
        existing.properties.update(entity.properties)
        if not existing.iri:
            existing.iri = entity.iri
        if not existing.name:
            existing.name = entity.name
        if not existing.namespace:
            existing.namespace = entity.namespace
        if existing.kind == "Resource" and entity.kind != "Resource":
            existing.kind = entity.kind
        return existing

    def add_literal(self, literal: LiteralValue) -> LiteralValue:
        self.literals.setdefault(literal.key, literal)
        return self.literals[literal.key]

    def add_relationship(self, relationship: OwlRelationship) -> None:
        self.relationships.append(relationship)

    def deduplicate_relationships(self) -> None:
        seen: set[tuple[str, str, str, tuple[tuple[str, str], ...], bool]] = set()
        deduped: list[OwlRelationship] = []
        for relationship in self.relationships:
            key = relationship.identity()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(relationship)
        self.relationships = deduped

    def summary(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for relationship in self.relationships:
            by_type[relationship.rel_type] = by_type.get(relationship.rel_type, 0) + 1
        by_kind: dict[str, int] = {}
        for entity in self.entities.values():
            by_kind[entity.kind] = by_kind.get(entity.kind, 0) + 1
        return {
            "source": self.source,
            "ontology_iri": self.ontology_iri,
            "entity_count": len(self.entities),
            "literal_count": len(self.literals),
            "relationship_count": len(self.relationships),
            "triple_count": self.triple_count,
            "entities_by_kind": by_kind,
            "relationships_by_type": by_type,
        }
