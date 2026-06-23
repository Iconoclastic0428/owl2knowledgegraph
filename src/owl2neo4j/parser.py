"""Parse OWL files with Owlready2 into Neo4j-ready records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from owlready2 import (
    EXACTLY,
    HAS_SELF,
    MAX,
    MIN,
    ONLY,
    SOME,
    VALUE,
    AnnotationPropertyClass,
    DataPropertyClass,
    ObjectPropertyClass,
    Or,
    And,
    Not,
    Restriction,
    ThingClass,
    World,
)
from rdflib import BNode, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from .model import LiteralValue, Neo4jProperty, OntologyGraph, OwlEntity, OwlRelationship


COMMON_PREDICATES = {
    str(RDF.type): "RDF_TYPE",
    str(RDFS.subClassOf): "RDFS_SUBCLASS_OF",
    str(RDFS.subPropertyOf): "RDFS_SUBPROPERTY_OF",
    str(RDFS.domain): "RDFS_DOMAIN",
    str(RDFS.range): "RDFS_RANGE",
    str(RDFS.label): "RDFS_LABEL",
    str(RDFS.comment): "RDFS_COMMENT",
    str(OWL.equivalentClass): "OWL_EQUIVALENT_CLASS",
    str(OWL.equivalentProperty): "OWL_EQUIVALENT_PROPERTY",
    str(OWL.disjointWith): "OWL_DISJOINT_WITH",
    str(OWL.inverseOf): "OWL_INVERSE_OF",
    str(OWL.onProperty): "OWL_ON_PROPERTY",
    str(OWL.someValuesFrom): "OWL_SOME_VALUES_FROM",
    str(OWL.allValuesFrom): "OWL_ALL_VALUES_FROM",
    str(OWL.hasValue): "OWL_HAS_VALUE",
    str(OWL.minCardinality): "OWL_MIN_CARDINALITY",
    str(OWL.maxCardinality): "OWL_MAX_CARDINALITY",
    str(OWL.cardinality): "OWL_CARDINALITY",
}

PYTHON_TYPE_TO_XSD = {
    str: str(XSD.string),
    int: str(XSD.integer),
    float: str(XSD.decimal),
    bool: str(XSD.boolean),
}

RESTRICTION_TYPES = {
    SOME: ("some", "SOME_VALUES_FROM"),
    ONLY: ("only", "ONLY_VALUES_FROM"),
    VALUE: ("value", "HAS_VALUE"),
    MIN: ("min", "MIN_CARDINALITY"),
    MAX: ("max", "MAX_CARDINALITY"),
    EXACTLY: ("exactly", "EXACT_CARDINALITY"),
    HAS_SELF: ("self", "HAS_SELF"),
}


class OwlOntologyParser:
    """Load an OWL ontology with Owlready2 and create a graph import plan.

    The parser emits two complementary layers:

    * a raw RDF layer where every triple loaded by Owlready2 becomes one
      Neo4j relationship, preserving predicates and literal values exactly;
    * a semantic OWL layer with query-friendly relationships such as
      ``SUBCLASS_OF``, ``INSTANCE_OF``, ``DOMAIN``, ``RANGE``, and restriction
      expression nodes.
    """

    def __init__(
        self,
        *,
        include_imports: bool = False,
        include_raw_triples: bool = True,
        include_semantic_edges: bool = True,
    ) -> None:
        self.include_imports = include_imports
        self.include_raw_triples = include_raw_triples
        self.include_semantic_edges = include_semantic_edges
        self._world: World | None = None
        self._expression_cache: dict[int, str] = {}

    def parse(self, source: str | Path) -> OntologyGraph:
        source_text = str(source)
        ontology_iri = self._source_to_ontology_iri(source)
        self._world = World()
        ontology = self._world.get_ontology(ontology_iri)
        source_path = self._source_path(source)
        if source_path:
            ontology = ontology.load(
                fileobj=source_path.open("rb"),
                only_local=not self.include_imports,
            )
        else:
            ontology = ontology.load(only_local=not self.include_imports)
        graph = OntologyGraph(source=source_text, ontology_iri=getattr(ontology, "base_iri", None))
        self._expression_cache = {}

        self._add_ontology_nodes(graph)
        if self.include_raw_triples:
            self._add_raw_triples(graph)
        if self.include_semantic_edges:
            self._add_semantic_owl(graph)
        graph.deduplicate_relationships()
        return graph

    def _source_to_ontology_iri(self, source: str | Path) -> str:
        if isinstance(source, Path):
            return source.resolve().as_uri()
        source_text = str(source)
        path = Path(source_text)
        if path.exists():
            return path.resolve().as_uri()
        return source_text

    def _source_path(self, source: str | Path) -> Path | None:
        path = source if isinstance(source, Path) else Path(str(source))
        return path.resolve() if path.exists() else None

    def _add_ontology_nodes(self, graph: OntologyGraph) -> None:
        assert self._world is not None
        for ontology in self._world.ontologies.values():
            iri = getattr(ontology, "base_iri", None) or getattr(ontology, "name", None)
            if not iri:
                continue
            graph.add_entity(
                OwlEntity(
                    key=self._iri_key(iri),
                    iri=iri,
                    kind="Ontology",
                    name=getattr(ontology, "name", None),
                    namespace=iri,
                    properties={"source": graph.source},
                )
            )

    def _add_raw_triples(self, graph: OntologyGraph) -> None:
        assert self._world is not None
        rdflib_graph = self._world.as_rdflib_graph()
        graph.triple_count = len(rdflib_graph)

        for subject, predicate, obj in rdflib_graph:
            subject_entity = self._entity_from_rdf_term(subject)
            graph.add_entity(subject_entity)

            predicate_iri = str(predicate)
            properties: dict[str, Neo4jProperty] = {
                "source": "rdf",
                "predicate_iri": predicate_iri,
                "predicate_name": iri_fragment(predicate_iri),
            }
            rel_type = raw_relationship_type(predicate_iri)

            if isinstance(obj, Literal):
                literal = literal_from_rdflib(obj)
                graph.add_literal(literal)
                if literal.datatype:
                    properties["object_datatype"] = literal.datatype
                if literal.language:
                    properties["object_language"] = literal.language
                graph.add_relationship(
                    OwlRelationship(
                        start_key=subject_entity.key,
                        end_key=literal.key,
                        rel_type=rel_type,
                        properties=properties,
                        end_is_literal=True,
                    )
                )
                continue

            object_entity = self._entity_from_rdf_term(obj)
            graph.add_entity(object_entity)
            graph.add_relationship(
                OwlRelationship(
                    start_key=subject_entity.key,
                    end_key=object_entity.key,
                    rel_type=rel_type,
                    properties=properties,
                )
            )

    def _add_semantic_owl(self, graph: OntologyGraph) -> None:
        assert self._world is not None
        ontologies = list(self._world.ontologies.values()) if self.include_imports else [self._primary_ontology()]

        for ontology in ontologies:
            for cls in ontology.classes():
                self._add_class(graph, cls)
            for prop in ontology.object_properties():
                self._add_property(graph, prop, "ObjectProperty")
            for prop in ontology.data_properties():
                self._add_property(graph, prop, "DataProperty")
            for prop in ontology.annotation_properties():
                self._add_property(graph, prop, "AnnotationProperty")
            for individual in ontology.individuals():
                self._add_individual(graph, individual)

    def _primary_ontology(self):
        assert self._world is not None
        ontologies = [onto for onto in self._world.ontologies.values() if getattr(onto, "loaded", False)]
        return ontologies[-1] if ontologies else next(iter(self._world.ontologies.values()))

    def _add_class(self, graph: OntologyGraph, cls: ThingClass) -> OwlEntity:
        cls_entity = self._entity_from_owl_object(cls, "Class")
        graph.add_entity(cls_entity)

        for parent in getattr(cls, "is_a", []):
            self._connect_class_expression(
                graph,
                cls_entity.key,
                parent,
                class_rel_type="SUBCLASS_OF",
                expression_rel_type="HAS_RESTRICTION",
            )

        for equivalent in getattr(cls, "equivalent_to", []):
            self._connect_class_expression(
                graph,
                cls_entity.key,
                equivalent,
                class_rel_type="EQUIVALENT_TO",
                expression_rel_type="EQUIVALENT_TO",
            )

        for disjoint in getattr(cls, "disjoint_with", None) or []:
            disjoint_entity = self._class_expression_node(graph, disjoint)
            if disjoint_entity:
                graph.add_relationship(
                    OwlRelationship(
                        cls_entity.key,
                        disjoint_entity.key,
                        "DISJOINT_WITH",
                        {"source": "owlready2"},
                    )
                )
        return cls_entity

    def _add_property(
        self,
        graph: OntologyGraph,
        prop: ObjectPropertyClass | DataPropertyClass | AnnotationPropertyClass,
        kind: str,
    ) -> OwlEntity:
        prop_entity = self._entity_from_owl_object(prop, kind)
        graph.add_entity(prop_entity)

        for parent in getattr(prop, "is_a", []):
            if parent is prop:
                continue
            parent_entity = self._entity_from_owl_object(parent, self._property_kind(parent))
            graph.add_entity(parent_entity)
            graph.add_relationship(
                OwlRelationship(
                    prop_entity.key,
                    parent_entity.key,
                    "SUBPROPERTY_OF",
                    {"source": "owlready2"},
                )
            )

        for domain in getattr(prop, "domain", []) or []:
            domain_entity = self._class_expression_node(graph, domain)
            if domain_entity:
                graph.add_relationship(
                    OwlRelationship(prop_entity.key, domain_entity.key, "DOMAIN", {"source": "owlready2"})
                )

        for range_value in getattr(prop, "range", []) or []:
            range_entity = self._class_expression_node(graph, range_value)
            if range_entity:
                graph.add_relationship(
                    OwlRelationship(prop_entity.key, range_entity.key, "RANGE", {"source": "owlready2"})
                )

        inverse = getattr(prop, "inverse_property", None)
        if inverse:
            inverse_entity = self._entity_from_owl_object(inverse, self._property_kind(inverse))
            graph.add_entity(inverse_entity)
            graph.add_relationship(
                OwlRelationship(prop_entity.key, inverse_entity.key, "INVERSE_OF", {"source": "owlready2"})
            )

        for equivalent in getattr(prop, "equivalent_to", []) or []:
            equivalent_entity = self._entity_from_owl_object(equivalent, self._property_kind(equivalent))
            graph.add_entity(equivalent_entity)
            graph.add_relationship(
                OwlRelationship(prop_entity.key, equivalent_entity.key, "EQUIVALENT_PROPERTY", {"source": "owlready2"})
            )
        return prop_entity

    def _add_individual(self, graph: OntologyGraph, individual) -> OwlEntity:
        individual_entity = self._entity_from_owl_object(individual, "Individual")
        graph.add_entity(individual_entity)

        for cls in getattr(individual, "is_a", []) or []:
            class_entity = self._class_expression_node(graph, cls)
            if class_entity:
                graph.add_relationship(
                    OwlRelationship(individual_entity.key, class_entity.key, "INSTANCE_OF", {"source": "owlready2"})
                )

        for prop in individual.get_properties():
            predicate_entity = self._entity_from_owl_object(prop, self._property_kind(prop))
            graph.add_entity(predicate_entity)
            predicate_iri = getattr(prop, "iri", None)
            properties: dict[str, Neo4jProperty] = {
                "source": "owlready2",
                "predicate_iri": predicate_iri or str(prop),
                "predicate_name": iri_fragment(predicate_iri or str(prop)),
            }
            for value in prop[individual]:
                if hasattr(value, "iri"):
                    value_entity = self._entity_from_owl_object(value, "Individual")
                    graph.add_entity(value_entity)
                    graph.add_relationship(
                        OwlRelationship(
                            individual_entity.key,
                            value_entity.key,
                            "OBJECT_PROPERTY_VALUE",
                            properties,
                        )
                    )
                else:
                    literal = literal_from_python(value)
                    graph.add_literal(literal)
                    graph.add_relationship(
                        OwlRelationship(
                            individual_entity.key,
                            literal.key,
                            "DATA_PROPERTY_VALUE",
                            properties,
                            end_is_literal=True,
                        )
                    )
        return individual_entity

    def _connect_class_expression(
        self,
        graph: OntologyGraph,
        owner_key: str,
        expression,
        *,
        class_rel_type: str,
        expression_rel_type: str,
    ) -> None:
        if isinstance(expression, ThingClass):
            parent_entity = self._entity_from_owl_object(expression, "Class")
            graph.add_entity(parent_entity)
            graph.add_relationship(
                OwlRelationship(owner_key, parent_entity.key, class_rel_type, {"source": "owlready2"})
            )
            return

        expression_entity = self._class_expression_node(graph, expression)
        if expression_entity:
            graph.add_relationship(
                OwlRelationship(owner_key, expression_entity.key, expression_rel_type, {"source": "owlready2"})
            )

    def _class_expression_node(self, graph: OntologyGraph, expression) -> OwlEntity | None:
        if isinstance(expression, ThingClass):
            entity = self._entity_from_owl_object(expression, "Class")
            graph.add_entity(entity)
            return entity

        if isinstance(expression, Restriction):
            return self._restriction_node(graph, expression)

        if isinstance(expression, (And, Or, Not)):
            return self._logical_expression_node(graph, expression)

        if isinstance(expression, type):
            iri = PYTHON_TYPE_TO_XSD.get(expression)
            entity = OwlEntity(
                key=self._iri_key(iri) if iri else f"datatype:python:{expression.__name__}",
                iri=iri,
                kind="Datatype",
                name=expression.__name__,
                namespace=iri_namespace(iri) if iri else "python:",
                properties={"source": "owlready2"},
            )
            graph.add_entity(entity)
            return entity

        if hasattr(expression, "iri"):
            entity = self._entity_from_owl_object(expression, self._guess_kind(expression))
            graph.add_entity(entity)
            return entity

        return None

    def _restriction_node(self, graph: OntologyGraph, restriction: Restriction) -> OwlEntity:
        cached = self._expression_cache.get(id(restriction))
        if cached:
            return graph.entities[cached]

        restriction_name, value_rel_type = RESTRICTION_TYPES.get(
            restriction.type, (str(restriction.type), "RESTRICTION_VALUE")
        )
        expression_text = str(restriction)
        key = "restriction:" + stable_hash(
            {
                "expression": expression_text,
                "property": getattr(getattr(restriction, "property", None), "iri", None),
                "type": restriction_name,
                "value": self._expression_fingerprint(getattr(restriction, "value", None)),
                "cardinality": getattr(restriction, "cardinality", None),
            }
        )
        self._expression_cache[id(restriction)] = key

        props: dict[str, Neo4jProperty] = {
            "source": "owlready2",
            "expression": expression_text,
            "restriction_type": restriction_name,
        }
        cardinality = getattr(restriction, "cardinality", None)
        if cardinality is not None:
            props["cardinality"] = cardinality

        entity = OwlEntity(key=key, iri=None, kind="Restriction", name=expression_text, properties=props)
        graph.add_entity(entity)

        prop = getattr(restriction, "property", None)
        if prop is not None:
            prop_entity = self._entity_from_owl_object(prop, self._property_kind(prop))
            graph.add_entity(prop_entity)
            graph.add_relationship(
                OwlRelationship(entity.key, prop_entity.key, "ON_PROPERTY", {"source": "owlready2"})
            )

        value = getattr(restriction, "value", None)
        if value is not None:
            if value_rel_type.endswith("CARDINALITY") and not hasattr(value, "iri"):
                literal = literal_from_python(value)
                graph.add_literal(literal)
                graph.add_relationship(
                    OwlRelationship(entity.key, literal.key, value_rel_type, {"source": "owlready2"}, True)
                )
            else:
                value_entity = self._class_expression_node(graph, value)
                if value_entity:
                    graph.add_relationship(
                        OwlRelationship(entity.key, value_entity.key, value_rel_type, {"source": "owlready2"})
                    )
        return entity

    def _logical_expression_node(self, graph: OntologyGraph, expression: And | Or | Not) -> OwlEntity:
        cached = self._expression_cache.get(id(expression))
        if cached:
            return graph.entities[cached]

        if isinstance(expression, And):
            expression_type = "intersection"
            operands = list(expression.Classes)
            rel_type = "OPERAND"
        elif isinstance(expression, Or):
            expression_type = "union"
            operands = list(expression.Classes)
            rel_type = "OPERAND"
        else:
            expression_type = "complement"
            operands = [expression.Class]
            rel_type = "COMPLEMENT_OF"

        key = "expression:" + stable_hash(
            {
                "type": expression_type,
                "operands": [self._expression_fingerprint(operand) for operand in operands],
            }
        )
        self._expression_cache[id(expression)] = key

        entity = OwlEntity(
            key=key,
            iri=None,
            kind="ClassExpression",
            name=str(expression),
            properties={
                "source": "owlready2",
                "expression": str(expression),
                "expression_type": expression_type,
            },
        )
        graph.add_entity(entity)

        for index, operand in enumerate(operands):
            operand_entity = self._class_expression_node(graph, operand)
            if operand_entity:
                graph.add_relationship(
                    OwlRelationship(
                        entity.key,
                        operand_entity.key,
                        rel_type,
                        {"source": "owlready2", "position": index},
                    )
                )
        return entity

    def _entity_from_rdf_term(self, term: URIRef | BNode) -> OwlEntity:
        if isinstance(term, URIRef):
            iri = str(term)
            return OwlEntity(
                key=self._iri_key(iri),
                iri=iri,
                kind=self._kind_from_iri(iri),
                name=iri_fragment(iri),
                namespace=iri_namespace(iri),
            )
        if isinstance(term, BNode):
            key = f"bnode:{term}"
            return OwlEntity(key=key, iri=None, kind="BlankNode", name=str(term))
        raise TypeError(f"Unsupported RDF entity term: {term!r}")

    def _entity_from_owl_object(self, obj, kind: str) -> OwlEntity:
        iri = getattr(obj, "iri", None)
        if iri:
            labels = normalized_values(getattr(obj, "label", []))
            comments = normalized_values(getattr(obj, "comment", []))
            properties: dict[str, Neo4jProperty] = {"source": "owlready2"}
            if labels:
                properties["labels"] = labels
                properties["preferred_label"] = labels[0]
            if comments:
                properties["comments"] = comments
            return OwlEntity(
                key=self._iri_key(iri),
                iri=iri,
                kind=kind,
                labels=set(labels),
                name=getattr(obj, "name", None) or iri_fragment(iri),
                namespace=str(getattr(getattr(obj, "namespace", None), "base_iri", None) or iri_namespace(iri)),
                properties=properties,
            )

        key = f"owlobj:{stable_hash(str(obj))}"
        return OwlEntity(key=key, iri=None, kind=kind, name=str(obj), properties={"source": "owlready2"})

    def _kind_from_iri(self, iri: str) -> str:
        assert self._world is not None
        obj = self._world[iri]
        if obj is None:
            return "Resource"
        return self._guess_kind(obj)

    def _guess_kind(self, obj) -> str:
        if isinstance(obj, ThingClass):
            return "Class"
        if isinstance(obj, ObjectPropertyClass):
            return "ObjectProperty"
        if isinstance(obj, DataPropertyClass):
            return "DataProperty"
        if isinstance(obj, AnnotationPropertyClass):
            return "AnnotationProperty"
        if isinstance(obj, Restriction):
            return "Restriction"
        if isinstance(obj, (And, Or, Not)):
            return "ClassExpression"
        if hasattr(obj, "iri"):
            return "Individual"
        return "Resource"

    def _property_kind(self, prop) -> str:
        if isinstance(prop, ObjectPropertyClass):
            return "ObjectProperty"
        if isinstance(prop, DataPropertyClass):
            return "DataProperty"
        if isinstance(prop, AnnotationPropertyClass):
            return "AnnotationProperty"
        return "Property"

    def _expression_fingerprint(self, expression) -> str | None:
        if expression is None:
            return None
        iri = getattr(expression, "iri", None)
        if iri:
            return iri
        if isinstance(expression, Restriction):
            return stable_hash(
                {
                    "restriction": str(expression),
                    "property": getattr(getattr(expression, "property", None), "iri", None),
                    "value": self._expression_fingerprint(getattr(expression, "value", None)),
                }
            )
        if isinstance(expression, (And, Or, Not)):
            return stable_hash(str(expression))
        return str(expression)

    def _iri_key(self, iri: str) -> str:
        return f"iri:{iri}"


def raw_relationship_type(predicate_iri: str) -> str:
    if predicate_iri in COMMON_PREDICATES:
        return COMMON_PREDICATES[predicate_iri]

    fragment = iri_fragment(predicate_iri) or "predicate"
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", fragment).strip("_").upper()
    if not slug:
        slug = "PREDICATE"
    if slug[0].isdigit():
        slug = f"P_{slug}"
    if len(slug) > 48:
        slug = f"{slug[:40]}_{stable_hash(predicate_iri)[:8]}"
    return f"RDF_{slug}"


def literal_from_rdflib(literal: Literal) -> LiteralValue:
    value = str(literal)
    datatype = str(literal.datatype) if literal.datatype else None
    language = literal.language
    key = "literal:" + stable_hash({"value": value, "datatype": datatype, "language": language})
    return LiteralValue(key=key, value=value, datatype=datatype, language=language)


def literal_from_python(value) -> LiteralValue:
    datatype = f"python:{type(value).__name__}"
    text = str(value)
    key = "literal:" + stable_hash({"value": text, "datatype": datatype, "language": None})
    return LiteralValue(key=key, value=text, datatype=datatype)


def normalized_values(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value)
        if text not in result:
            result.append(text)
    return result


def iri_fragment(iri: str) -> str:
    iri = str(iri)
    for delimiter in ("#", "/"):
        if delimiter in iri:
            tail = iri.rsplit(delimiter, 1)[1]
            if tail:
                return tail
    return iri


def iri_namespace(iri: str) -> str:
    iri = str(iri)
    for delimiter in ("#", "/"):
        if delimiter in iri:
            return iri.rsplit(delimiter, 1)[0] + delimiter
    return iri


def stable_hash(value: object) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
