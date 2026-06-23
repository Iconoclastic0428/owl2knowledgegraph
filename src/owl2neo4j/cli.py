"""Command line interface for owl2neo4j."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .loader import Neo4jOntologyLoader
from .parser import OwlOntologyParser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="owl2neo4j",
        description="Load an OWL ontology with Owlready2 and import it into Neo4j.",
    )
    parser.add_argument("source", help="Path or URL of the OWL ontology")
    parser.add_argument("--include-imports", action="store_true", help="Load owl:imports closure")
    parser.add_argument("--no-raw-triples", action="store_true", help="Do not emit the lossless raw RDF layer")
    parser.add_argument("--no-semantic-edges", action="store_true", help="Do not emit Owlready2 semantic edges")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print a JSON summary without loading Neo4j")
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI"), help="Neo4j URI, e.g. bolt://localhost:7687")
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"), help="Neo4j username")
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD"), help="Neo4j password")
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE"), help="Neo4j database name")
    parser.add_argument("--batch-size", type=int, default=1_000, help="Neo4j batch size")
    parser.add_argument("--import-id", help="Stable import id stored on all created nodes and relationships")
    parser.add_argument("--clear-existing", action="store_true", help="Delete nodes from the same import id before load")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.source) if Path(args.source).exists() else args.source

    parser = OwlOntologyParser(
        include_imports=args.include_imports,
        include_raw_triples=not args.no_raw_triples,
        include_semantic_edges=not args.no_semantic_edges,
    )
    graph = parser.parse(source)
    summary = graph.summary()

    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if not args.neo4j_uri or not args.neo4j_password:
        raise SystemExit("Neo4j load requires --neo4j-uri and --neo4j-password, or NEO4J_URI/NEO4J_PASSWORD.")

    with Neo4jOntologyLoader(
        args.neo4j_uri,
        args.neo4j_user,
        args.neo4j_password,
        database=args.neo4j_database,
        batch_size=args.batch_size,
    ) as loader:
        stats = loader.load(
            graph,
            import_id=args.import_id,
            clear_existing=args.clear_existing,
        )

    result = {**summary, "load": stats.__dict__}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
