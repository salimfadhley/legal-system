"""Observability (M12, ADR 0008): make the autonomous pipeline auditable and
gap-detecting so no document fails silently — completeness is a correctness property
for a legal evidence corpus.

This package holds reconciliation (expected vs actual → gaps), the pipeline event
model, and the audit/trace surface. See doc/decisions/0008-observability-architecture.md.
"""
