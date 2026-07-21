"""Streamlit operations dashboard (M13, ADR 0009).

Renders the canonical :class:`~goldberg_system.observability.state.SystemState` — the
*same* object as ``goldberg status`` — so the human view and the LLM YAML never drift.
Read-only over Elasticsearch; the one write action (DLQ reprocess) is a later increment
with the NATS DLQ.

    streamlit run -m goldberg_system.dashboard.app
"""

from __future__ import annotations

import os

import streamlit as st

from goldberg_system.observability.state import SystemState, aggregate


def _client():  # type: ignore[no-untyped-def]
    from elasticsearch import Elasticsearch

    return Elasticsearch(os.environ.get("GOLDBERG_ES_URL", "http://192.168.86.31:9200"))


@st.cache_data(ttl=10)
def _state() -> dict:
    return aggregate(_client()).model_dump()


def render(state: SystemState) -> None:
    """Render the dashboard from a SystemState (pure w.r.t. the model → testable-ish)."""
    st.set_page_config(
        page_title="Goldberg — system status", page_icon="📊", layout="wide"
    )
    st.title("Goldberg pipeline — operations")

    health = state.health
    status = health["status"]
    banner = {"ok": st.success, "degraded": st.warning}.get(status, st.error)
    banner(f"health: {status.upper()}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("documents", state.corpus["documents"])
    c2.metric("wiki pages", state.wiki["pages"])
    c3.metric("failed", state.dlq["failed"])
    c4.metric("skipped", state.dlq["skipped"])

    left, right = st.columns(2)
    with left:
        st.subheader("Corpus by matter")
        st.bar_chart(state.corpus["by_matter"])
        st.subheader("By document type")
        st.bar_chart(state.corpus["by_type"])
    with right:
        st.subheader("Pipeline stages")
        st.bar_chart(state.pipeline["by_stage_status"])
        st.subheader("Wiki by layer")
        st.bar_chart(state.wiki["by_layer"])

    st.subheader("Dead-letter / failures")
    recent = state.dlq["recent"]
    if recent:
        st.dataframe(recent, use_container_width=True)
    else:
        st.caption("no failed or skipped documents 🎉")

    with st.expander("LLM-readable state (YAML) — the same data"):
        st.code(state.to_yaml(), language="yaml")

    st.caption(
        f"generated {state.generated_at} · last indexed {state.pipeline['last_indexed_at']}"
    )


def main() -> None:
    render(SystemState(**_state()))


# `streamlit run <file>` executes the script as __main__.
if __name__ == "__main__":
    main()
