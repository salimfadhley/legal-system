"""The live-index service (ADR 0005) — Papra webhook → enrich → index."""

from goldberg_system.service.processor import Processor
from goldberg_system.service.webhook import parse_papra_event

__all__ = ["Processor", "parse_papra_event"]
