"""Explicitly seed Cloud Firestore with the bundled synthetic resolution cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from integrations import RuntimeConfig
from vector_store import EmbeddingClient, FirestoreVectorStore, case_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-candidates",
        action="store_true",
        help="Include the 20 corrected Generation 1 candidates for a developer/staging corpus.",
    )
    args = parser.parse_args()
    config = RuntimeConfig()
    if not config.is_firestore_ready:
        raise SystemExit("Set FIREBASE_PROJECT_ID before seeding Firestore")

    sources = [Path(__file__).with_name("mock_dataset.json")]
    if args.include_candidates:
        sources.append(Path(__file__).with_name("knowledge_cases_candidates.json"))
    cases: list[dict[str, object]] = []
    for dataset_path in sources:
        cases.extend(json.loads(dataset_path.read_text(encoding="utf-8")))
    embedder = EmbeddingClient(config)
    vectors = embedder.embed_many([case_text(case) for case in cases])
    FirestoreVectorStore(config).upsert(cases, vectors)
    classification = "developer candidates" if args.include_candidates else "synthetic verified demos"
    print(f"Seeded {len(cases)} {classification} into {config.firestore_collection}.")


if __name__ == "__main__":
    main()
