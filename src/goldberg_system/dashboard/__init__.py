"""M13 operations dashboard (ADR 0009) — the human renderer of the M12 SystemState.

The dashboard *only renders* observability data (it never generates telemetry): the
same ``observability.state.aggregate()`` that powers ``goldberg status`` feeds the
Streamlit UI here and the LLM-readable YAML, so the two modes cannot drift.

Run locally:  ``streamlit run -m goldberg_system.dashboard.app``  (needs the
``dashboard`` optional dependency: ``uv sync --extra dashboard``).
"""
