from __future__ import annotations

from owl2neo4j.loader import Neo4jOntologyLoader, relationship_key
from owl2neo4j.parser import OwlOntologyParser


class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def run(self, query: str, **params):
        self.calls.append((query, params))
        return []


class RecordingDriver:
    def __init__(self) -> None:
        self.session_obj = RecordingSession()

    def session(self, database=None):
        self.database = database
        return self.session_obj

    def close(self) -> None:
        return None


def test_loader_batches_resources_literals_and_relationships(foodon_like_owl):
    graph = OwlOntologyParser().parse(foodon_like_owl)
    driver = RecordingDriver()
    loader = Neo4jOntologyLoader(driver=driver, batch_size=3)

    stats = loader.load(graph, import_id="test-import", clear_existing=True)

    assert stats.resources == len(graph.entities)
    assert stats.literals == len(graph.literals)
    assert stats.relationships == len(graph.relationships)
    assert stats.import_id == "test-import"

    calls = driver.session_obj.calls
    assert any("CREATE CONSTRAINT owl2neo4j_resource_key" in query for query, _ in calls)
    assert any("DETACH DELETE" in query for query, _ in calls)
    assert any("MERGE (n:OwlResource" in query for query, _ in calls)
    assert any("MERGE (n:OwlLiteral" in query for query, _ in calls)
    assert any("MATCH (o:OwlResource" in query for query, _ in calls)
    assert any("MATCH (o:OwlLiteral" in query for query, _ in calls)

    loaded_relationship_rows = sum(
        len(params["rows"])
        for query, params in calls
        if "MERGE (s)-[r:" in query
    )
    assert loaded_relationship_rows == len(graph.relationships)


def test_relationship_key_is_stable_and_import_scoped(foodon_like_owl):
    graph = OwlOntologyParser().parse(foodon_like_owl)
    relationship = graph.relationships[0]

    assert relationship_key(relationship, "a") == relationship_key(relationship, "a")
    assert relationship_key(relationship, "a") != relationship_key(relationship, "b")
