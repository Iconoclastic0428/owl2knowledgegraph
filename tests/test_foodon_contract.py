from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from owl2neo4j.loader import Neo4jOntologyLoader
from owl2neo4j.parser import OwlOntologyParser


def foodon_path() -> Path:
    path = os.getenv("FOODON_OWL_PATH")
    if not path:
        pytest.skip("Set FOODON_OWL_PATH and RUN_FOODON_TESTS=1 to run the FoodOn contract test.")
    resolved = Path(path)
    if not resolved.exists():
        pytest.skip(f"FOODON_OWL_PATH does not exist: {resolved}")
    return resolved


@pytest.mark.skipif(os.getenv("RUN_FOODON_TESTS") != "1", reason="FoodOn test is opt-in.")
def test_foodon_raw_layer_keeps_every_predicate_and_triple():
    graph = OwlOntologyParser(include_semantic_edges=False).parse(foodon_path())
    raw_relationships = [rel for rel in graph.relationships if rel.properties.get("source") == "rdf"]

    assert graph.triple_count > 100_000
    assert len(raw_relationships) == graph.triple_count

    predicate_counts = Counter(rel.properties["predicate_iri"] for rel in raw_relationships)
    assert sum(predicate_counts.values()) == graph.triple_count
    assert predicate_counts["http://www.w3.org/1999/02/22-rdf-syntax-ns#type"] > 0
    assert predicate_counts["http://www.w3.org/2000/01/rdf-schema#subClassOf"] > 0
    assert predicate_counts["http://www.w3.org/2000/01/rdf-schema#label"] > 0
    assert predicate_counts["http://purl.obolibrary.org/obo/IAO_0000115"] > 0
    assert len(predicate_counts) > 25


@pytest.mark.skipif(os.getenv("RUN_FOODON_NEO4J_TESTS") != "1", reason="FoodOn Neo4j test is opt-in.")
def test_foodon_loads_expected_counts_into_real_neo4j():
    uri = os.getenv("NEO4J_URI")
    password = os.getenv("NEO4J_PASSWORD")
    user = os.getenv("NEO4J_USER", "neo4j")
    database = os.getenv("NEO4J_DATABASE")
    if not uri or not password:
        pytest.skip("Set NEO4J_URI and NEO4J_PASSWORD to run the real Neo4j FoodOn test.")

    graph = OwlOntologyParser(include_semantic_edges=False).parse(foodon_path())
    import_id = os.getenv("FOODON_IMPORT_ID", "foodon-contract-test")

    with Neo4jOntologyLoader(uri, user, password, database=database, batch_size=5_000) as loader:
        stats = loader.load(graph, import_id=import_id, clear_existing=True)
        with loader.driver.session(database=database) as session:
            resource_count = session.run(
                "MATCH (n:OwlResource {import_id: $import_id}) RETURN count(n) AS count",
                import_id=import_id,
            ).single()["count"]
            literal_count = session.run(
                "MATCH (n:OwlLiteral {import_id: $import_id}) RETURN count(n) AS count",
                import_id=import_id,
            ).single()["count"]
            relationship_count = session.run(
                "MATCH ()-[r {import_id: $import_id}]->() RETURN count(r) AS count",
                import_id=import_id,
            ).single()["count"]

    assert stats.resources == len(graph.entities)
    assert stats.literals == len(graph.literals)
    assert stats.relationships == len(graph.relationships)
    assert resource_count == len(graph.entities)
    assert literal_count == len(graph.literals)
    assert relationship_count == len(graph.relationships)


@pytest.mark.skipif(os.getenv("RUN_FOODON_NEO4J_TESTS") != "1", reason="FoodOn Neo4j test is opt-in.")
def test_foodon_full_graph_matches_parser_counts_in_real_neo4j():
    uri = os.getenv("NEO4J_URI")
    password = os.getenv("NEO4J_PASSWORD")
    user = os.getenv("NEO4J_USER", "neo4j")
    database = os.getenv("NEO4J_DATABASE")
    if not uri or not password:
        pytest.skip("Set NEO4J_URI and NEO4J_PASSWORD to run the real Neo4j FoodOn test.")

    graph = OwlOntologyParser().parse(foodon_path())
    import_id = os.getenv("FOODON_FULL_IMPORT_ID", "foodon-full-contract-test")
    expected_kinds = Counter(entity.kind for entity in graph.entities.values())
    expected_raw_predicates = Counter(
        rel.properties["predicate_iri"]
        for rel in graph.relationships
        if rel.properties.get("source") == "rdf"
    )
    expected_semantic_types = Counter(
        rel.rel_type
        for rel in graph.relationships
        if rel.properties.get("source") == "owlready2"
    )

    with Neo4jOntologyLoader(uri, user, password, database=database, batch_size=5_000) as loader:
        loader.load(graph, import_id=import_id, clear_existing=True)
        with loader.driver.session(database=database) as session:
            resource_count = session.run(
                "MATCH (n:OwlResource {import_id: $import_id}) RETURN count(n) AS count",
                import_id=import_id,
            ).single()["count"]
            literal_count = session.run(
                "MATCH (n:OwlLiteral {import_id: $import_id}) RETURN count(n) AS count",
                import_id=import_id,
            ).single()["count"]
            relationship_count = session.run(
                "MATCH ()-[r {import_id: $import_id}]->() RETURN count(r) AS count",
                import_id=import_id,
            ).single()["count"]
            actual_kinds = Counter(
                {
                    record["kind"]: record["count"]
                    for record in session.run(
                        """
                        MATCH (n:OwlResource {import_id: $import_id})
                        RETURN n.kind AS kind, count(n) AS count
                        """,
                        import_id=import_id,
                    )
                }
            )
            actual_raw_predicates = Counter(
                {
                    record["predicate"]: record["count"]
                    for record in session.run(
                        """
                        MATCH ()-[r {import_id: $import_id}]->()
                        WHERE r.source = "rdf"
                        RETURN r.predicate_iri AS predicate, count(r) AS count
                        """,
                        import_id=import_id,
                    )
                }
            )
            actual_semantic_types = Counter(
                {
                    record["type"]: record["count"]
                    for record in session.run(
                        """
                        MATCH ()-[r {import_id: $import_id}]->()
                        WHERE r.source = "owlready2"
                        RETURN type(r) AS type, count(r) AS count
                        """,
                        import_id=import_id,
                    )
                }
            )

    assert resource_count == len(graph.entities)
    assert literal_count == len(graph.literals)
    assert relationship_count == len(graph.relationships)
    assert actual_kinds == expected_kinds
    assert actual_raw_predicates == expected_raw_predicates
    assert actual_semantic_types == expected_semantic_types
    assert actual_raw_predicates["http://purl.obolibrary.org/obo/IAO_0000115"] > 0
    assert actual_semantic_types["SUBCLASS_OF"] > 0
    assert actual_semantic_types["HAS_RESTRICTION"] > 0
