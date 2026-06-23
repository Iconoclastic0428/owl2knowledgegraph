from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import Request, urlopen


FOODON_URL = "https://purl.obolibrary.org/obo/foodon.owl"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the official FoodOn OWL file.")
    parser.add_argument("output", type=Path, help="Destination .owl path")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    request = Request(FOODON_URL, headers={"User-Agent": "owl2neo4j-foodon-test"})
    with urlopen(request, timeout=120) as response, args.output.open("wb") as handle:
        handle.write(response.read())
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
