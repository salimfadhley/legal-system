"""Entrypoint for the live-index service: build from env and serve."""

from __future__ import annotations

import logging
import os

from goldberg_system.enrichment import OpenAIEnricher
from goldberg_system.papra import PapraClient
from goldberg_system.service.app import run
from goldberg_system.service.processor import Processor
from goldberg_system.sinks import ElasticsearchIndexer


def build_processor() -> Processor:
    """Build the processor from the environment (Papra / OpenAI / ES)."""
    papra = PapraClient.from_env()
    enricher = OpenAIEnricher.from_settings()
    indexer = ElasticsearchIndexer.from_env()
    indexer.ensure_index()
    return Processor(papra, enricher, [indexer])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    run(build_processor(), host, port)


if __name__ == "__main__":
    main()
