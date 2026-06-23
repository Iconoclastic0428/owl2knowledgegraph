# owl2neo4j

Load an OWL file with Owlready2 and materialize it as a Neo4j knowledge graph.

The importer writes two layers:

1. A lossless RDF layer: every triple loaded by Owlready2 becomes one Neo4j relationship. Literal objects become `:OwlLiteral` nodes. Each relationship stores `predicate_iri`, `predicate_name`, and `source: "rdf"`.
2. A semantic OWL layer: classes, properties, individuals, restrictions, and class expressions get query-friendly labels and relationships such as `SUBCLASS_OF`, `INSTANCE_OF`, `DOMAIN`, `RANGE`, `HAS_RESTRICTION`, `ON_PROPERTY`, and `SOME_VALUES_FROM`.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

## Load an OWL File

Dry run:

```powershell
owl2neo4j path\to\ontology.owl --dry-run
```

Load into Neo4j:

```powershell
$env:NEO4J_URI = "bolt://localhost:7687"
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASSWORD = "password"

owl2neo4j path\to\ontology.owl --clear-existing --import-id my-ontology
```

To include `owl:imports`, add:

```powershell
owl2neo4j path\to\ontology.owl --include-imports
```

## Python API

```python
from owl2neo4j import Neo4jOntologyLoader, OwlOntologyParser

graph = OwlOntologyParser(include_imports=False).parse("foodon.owl")

with Neo4jOntologyLoader("bolt://localhost:7687", "neo4j", "password") as loader:
    stats = loader.load(graph, import_id="foodon", clear_existing=True)

print(stats)
```

## Neo4j Shape

Resource nodes:

```cypher
(:OwlResource {
  key: "iri:http://purl.obolibrary.org/obo/FOODON_00002403",
  iri: "http://purl.obolibrary.org/obo/FOODON_00002403",
  kind: "Class",
  name: "FOODON_00002403",
  preferred_label: "food product",
  import_id: "foodon"
})
```

Extra labels are added by kind, for example `:OwlClass`, `:OwlObjectProperty`, `:OwlDataProperty`, `:OwlAnnotationProperty`, `:OwlIndividual`, `:OwlRestriction`, and `:OwlClassExpression`.

Literal nodes:

```cypher
(:OwlLiteral {
  key: "literal:...",
  value: "food product",
  datatype: "http://www.w3.org/2001/XMLSchema#string",
  language: "en",
  import_id: "foodon"
})
```

Raw RDF relationships preserve every predicate:

```cypher
(c:OwlClass)-[:RDFS_LABEL {
  predicate_iri: "http://www.w3.org/2000/01/rdf-schema#label",
  source: "rdf"
}]->(label:OwlLiteral)
```

Semantic relationships make common OWL queries easier:

```cypher
(child:OwlClass)-[:SUBCLASS_OF {source: "owlready2"}]->(parent:OwlClass)
(class:OwlClass)-[:HAS_RESTRICTION]->(:OwlRestriction)-[:ON_PROPERTY]->(:OwlObjectProperty)
```

## FoodOn Verification

Download FoodOn:

```powershell
python scripts\download_foodon.py ..\..\work\foodon.owl
```

Run the fast tests:

```powershell
python -m pytest . --basetemp ..\..\work\pytest-tmp
```

Run the FoodOn contract test:

```powershell
$env:RUN_FOODON_TESTS = "1"
$env:FOODON_OWL_PATH = "C:\path\to\foodon.owl"
python -m pytest tests\test_foodon_contract.py::test_foodon_raw_layer_keeps_every_predicate_and_triple --basetemp ..\..\work\pytest-foodon-tmp
```

Run the real Neo4j FoodOn integration test:

```powershell
$env:RUN_FOODON_NEO4J_TESTS = "1"
$env:FOODON_OWL_PATH = "C:\path\to\foodon.owl"
$env:NEO4J_URI = "bolt://localhost:7687"
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASSWORD = "password"
python -m pytest tests\test_foodon_contract.py::test_foodon_loads_expected_counts_into_real_neo4j --basetemp ..\..\work\pytest-foodon-neo4j-tmp
```

On the local FoodOn file downloaded from `https://purl.obolibrary.org/obo/foodon.owl`, the raw-layer verification passed with:

```text
401,441 RDF triples
401,441 raw Neo4j-ready relationships
103,187 resource nodes
101,972 literal nodes
```

The full database-backed FoodOn verification was run against a Neo4j Docker container on `bolt://localhost:17687` and passed exact count comparisons:

```text
113,343 resource nodes
102,076 literal nodes
483,398 total relationships
401,441 raw RDF relationships
81,957 semantic Owlready2 relationships
63 distinct RDF predicates
16 distinct semantic relationship types
```

The test also compares every raw `predicate_iri` count and every semantic Neo4j relationship type count between the parser output and the Neo4j database.

## Query Examples

Find FoodOn definitions:

```cypher
MATCH (c:OwlClass)-[r]->(definition:OwlLiteral)
WHERE r.predicate_iri = "http://purl.obolibrary.org/obo/IAO_0000115"
RETURN c.iri, c.preferred_label, definition.value
LIMIT 25
```

Find subclass parents:

```cypher
MATCH (child:OwlClass {iri: "http://purl.obolibrary.org/obo/FOODON_00002403"})
      -[:SUBCLASS_OF|RDFS_SUBCLASS_OF]->(parent:OwlResource)
RETURN parent.iri, parent.preferred_label
```

Find OWL restrictions:

```cypher
MATCH (c:OwlClass)-[:HAS_RESTRICTION]->(r:OwlRestriction)
      -[:ON_PROPERTY]->(p:OwlResource)
OPTIONAL MATCH (r)-[:SOME_VALUES_FROM|ONLY_VALUES_FROM|HAS_VALUE]->(v:OwlResource)
RETURN c.preferred_label, r.restriction_type, p.iri, v.iri
LIMIT 25
```
