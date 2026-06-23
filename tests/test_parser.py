from __future__ import annotations

from collections import Counter

from owl2neo4j.parser import OwlOntologyParser


def test_parser_preserves_every_raw_triple(foodon_like_owl):
    graph = OwlOntologyParser(include_semantic_edges=False).parse(foodon_like_owl)

    raw_relationships = [rel for rel in graph.relationships if rel.properties.get("source") == "rdf"]
    assert graph.triple_count > 0
    assert len(raw_relationships) == graph.triple_count

    predicate_counts = Counter(rel.properties["predicate_iri"] for rel in raw_relationships)
    assert predicate_counts["http://www.w3.org/2000/01/rdf-schema#subClassOf"] == 2
    assert predicate_counts["http://purl.obolibrary.org/obo/IAO_0000115"] == 1
    assert predicate_counts["http://purl.obolibrary.org/obo/RO_0001000"] == 1
    assert predicate_counts["http://example.org/foodon-test.owl#hasQuality"] == 1


def test_parser_extracts_semantic_owl_relationships(foodon_like_owl):
    graph = OwlOntologyParser().parse(foodon_like_owl)
    relationships = {(rel.start_key, rel.rel_type, rel.end_key) for rel in graph.relationships}
    kinds = {entity.iri: entity.kind for entity in graph.entities.values() if entity.iri}

    assert kinds["http://purl.obolibrary.org/obo/FOODON_00002403"] == "Class"
    assert kinds["http://purl.obolibrary.org/obo/RO_0001000"] == "ObjectProperty"
    assert kinds["http://example.org/foodon-test.owl#hasQuality"] == "DataProperty"
    assert kinds["http://purl.obolibrary.org/obo/IAO_0000115"] == "AnnotationProperty"
    assert kinds["http://example.org/foodon-test.owl#sample_food"] == "Individual"

    product = "iri:http://purl.obolibrary.org/obo/FOODON_00002403"
    material = "iri:http://purl.obolibrary.org/obo/FOODON_00001002"
    derives_from = "iri:http://purl.obolibrary.org/obo/RO_0001000"
    sample_food = "iri:http://example.org/foodon-test.owl#sample_food"
    sample_ingredient = "iri:http://example.org/foodon-test.owl#sample_ingredient"

    assert (product, "SUBCLASS_OF", material) in relationships
    assert any(start == product and rel_type == "HAS_RESTRICTION" for start, rel_type, _ in relationships)
    assert any(start != product and rel_type == "ON_PROPERTY" and end == derives_from for start, rel_type, end in relationships)
    assert (sample_food, "INSTANCE_OF", product) in relationships
    assert (sample_food, "OBJECT_PROPERTY_VALUE", sample_ingredient) in relationships
    assert any(start == sample_food and rel_type == "DATA_PROPERTY_VALUE" for start, rel_type, _ in relationships)
