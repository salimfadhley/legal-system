"""Component-health probes — the ``goldberg doctor`` liveness board (ADR 0008).

Answers "is the pipeline actually up?" by probing every major component
(Elasticsearch, Docling OCR, the enricher, the MCP server, and the live-index
watcher) and assembling a :class:`DoctorReport`. Each probe is:

* **individually time-bounded** — an internal request timeout of ≤5s, plus an outer
  board deadline (:data:`DEFAULT_BOARD_TIMEOUT`) enforced by :func:`run_doctor`, so a
  hung component cannot hang the report (NFR-001);
* **read-only** — it only reads liveness/health/count endpoints, never writing,
  deleting or mutating anything (NFR-002); and
* **crash-proof** — it catches everything and turns any failure into a ``DOWN`` with a
  reason string, so one broken component never breaks the board (C-003).

All probes run **concurrently** on a thread pool, so the whole board returns within
its budget even when several components are down. The same report backs
``goldberg doctor``, the compact section in ``goldberg status``, and (via the same
functions) the MCP layer — one implementation, no drift (C-001).
"""

from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter
from typing import Any

import requests
import yaml
from pydantic import BaseModel

from goldberg_system.provenance import now_iso

# Per-probe internal timeout and the outer board deadline (NFR-001).
DEFAULT_PROBE_TIMEOUT = 5.0
DEFAULT_BOARD_TIMEOUT = 10.0
# Live-index-watcher liveness is *inferred* from pipeline-event freshness: an event
# newer than this window means the remote watcher is (almost certainly) alive.
DEFAULT_FRESHNESS_SECONDS = 15 * 60

DOCUMENTS_INDEX = "goldberg_documents"
EVENTS_INDEX = "goldberg_pipeline_events"
REQUIRED_INDICES = (DOCUMENTS_INDEX, EVENTS_INDEX)

# The reachable MCP address in this deployment; used when the configured bind host is
# the wildcard ``0.0.0.0`` (which you cannot actually connect to).
_MCP_FALLBACK_HOST = "192.168.86.31"

# Order the components appear on the board.
PROBE_ORDER = (
    "elasticsearch",
    "docling",
    "enricher",
    "mcp_server",
    "live_index_watcher",
)


class ComponentStatus(str, Enum):
    """Liveness of a component. Ordered by severity for the overall verdict."""

    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"

    @property
    def severity(self) -> int:
        return {"UP": 0, "DEGRADED": 1, "DOWN": 2}[self.value]


class ComponentHealth(BaseModel):
    """One component's probe result — status, a one-line reason and latency.

    ``inferred`` marks a status that could not be probed directly and was deduced
    from a proxy signal (the live-index watcher, on a remote host, is inferred from
    pipeline-event freshness).
    """

    name: str
    status: ComponentStatus
    detail: str
    latency_ms: float | None = None
    inferred: bool = False


class DoctorReport(BaseModel):
    """The whole board — every component plus the worst-status overall verdict."""

    generated_at: str
    components: list[ComponentHealth]
    overall: ComponentStatus

    def to_yaml(self) -> str:
        # mode="json" renders the enums as their string values so PyYAML can dump them.
        return yaml.safe_dump(
            self.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        )


def worst_status(statuses: Iterable[ComponentStatus]) -> ComponentStatus:
    """Return the worst status (DOWN > DEGRADED > UP); UP when empty."""
    worst = ComponentStatus.UP
    for status in statuses:
        if status.severity > worst.severity:
            worst = status
    return worst


# ── shared helpers ───────────────────────────────────────────────────────────


def _elapsed_ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 1)


def _es_client() -> Any:
    """The shared Elasticsearch client (reuses ``CorpusQuery`` env handling, C-001)."""
    from goldberg_system.query import CorpusQuery

    return CorpusQuery.from_env().client


def _index_count(client: Any, index: str) -> int | None:
    """Doc count for ``index``, or ``None`` when the index is absent/unreadable."""
    try:
        return int(client.count(index=index)["count"])
    except Exception:  # noqa: BLE001 - a missing index is "None", never a crash
        return None


def _age_seconds(ts_iso: str | None) -> float | None:
    """Seconds between now and an ISO-8601 timestamp; ``None`` if unparseable."""
    if not ts_iso:
        return None
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


# ── probes (each returns a ComponentHealth and never raises) ──────────────────


def probe_elasticsearch(
    client: Any | None = None, *, timeout: float = DEFAULT_PROBE_TIMEOUT
) -> ComponentHealth:
    """ES liveness: cluster responds and every required index exists.

    Unreachable/timeout → DOWN; reachable but a required index missing → DEGRADED;
    all present → UP (cluster status + document count).
    """
    name = "elasticsearch"
    start = perf_counter()
    if client is None:
        client = _es_client()
    try:
        resp = client.cluster.health(request_timeout=timeout)
    except TypeError:
        # a fake/older client without the request_timeout kwarg
        try:
            resp = client.cluster.health()
        except Exception as exc:  # noqa: BLE001
            return ComponentHealth(
                name=name,
                status=ComponentStatus.DOWN,
                detail=f"cluster unreachable: {exc}",
                latency_ms=_elapsed_ms(start),
            )
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DOWN,
            detail=f"cluster unreachable: {exc}",
            latency_ms=_elapsed_ms(start),
        )

    cluster_status = resp.get("status", "?") if isinstance(resp, dict) else "?"
    counts = {idx: _index_count(client, idx) for idx in REQUIRED_INDICES}
    missing = [idx for idx, count in counts.items() if count is None]
    latency = _elapsed_ms(start)
    if missing:
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DEGRADED,
            detail=f"cluster {cluster_status}; missing index: {', '.join(missing)}",
            latency_ms=latency,
        )
    return ComponentHealth(
        name=name,
        status=ComponentStatus.UP,
        detail=f"cluster {cluster_status}; {counts[DOCUMENTS_INDEX]} documents",
        latency_ms=latency,
    )


def probe_docling(
    docling: Any | None = None, *, timeout: float = DEFAULT_PROBE_TIMEOUT
) -> ComponentHealth:
    """Docling OCR liveness via ``DoclingClient.health()``. ok → UP else DOWN."""
    name = "docling"
    start = perf_counter()
    try:
        if docling is None:
            from goldberg_system.extract.docling_client import DoclingClient

            docling = DoclingClient.from_env()
        ok = docling.health()
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DOWN,
            detail=f"error: {exc}",
            latency_ms=_elapsed_ms(start),
        )
    base = getattr(docling, "base_url", "?")
    latency = _elapsed_ms(start)
    if ok:
        return ComponentHealth(
            name=name,
            status=ComponentStatus.UP,
            detail=f"health ok ({base})",
            latency_ms=latency,
        )
    return ComponentHealth(
        name=name,
        status=ComponentStatus.DOWN,
        detail=f"health check failed ({base})",
        latency_ms=latency,
    )


def probe_enricher(
    *, api_key: str | None = None, timeout: float = DEFAULT_PROBE_TIMEOUT
) -> ComponentHealth:
    """Enricher (OpenAI) reachability via a *metadata* call only (NFR-003).

    No key → DEGRADED (nothing to spend, but not fatal); ``GET /v1/models`` 200 → UP;
    any other status / unreachable → DOWN. Never a completion — no tokens billed.
    """
    name = "enricher"
    start = perf_counter()
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DEGRADED,
            detail="no OPENAI_API_KEY configured",
            latency_ms=_elapsed_ms(start),
        )
    try:
        resp = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DOWN,
            detail=f"unreachable: {exc}",
            latency_ms=_elapsed_ms(start),
        )
    latency = _elapsed_ms(start)
    if resp.status_code == 200:
        return ComponentHealth(
            name=name,
            status=ComponentStatus.UP,
            detail="openai reachable (models list)",
            latency_ms=latency,
        )
    return ComponentHealth(
        name=name,
        status=ComponentStatus.DOWN,
        detail=f"openai returned HTTP {resp.status_code}",
        latency_ms=latency,
    )


def probe_mcp_server(
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> ComponentHealth:
    """MCP server liveness via a real streamable-http ``initialize`` handshake.

    HTTP 200 (with an ``Mcp-Session-Id``) → UP; connection refused/timeout/non-200 →
    DOWN. The wildcard bind host ``0.0.0.0`` is rewritten to the reachable address.
    """
    name = "mcp_server"
    start = perf_counter()
    host = host or os.environ.get("GOLDBERG_MCP_HOST", "0.0.0.0")
    if host == "0.0.0.0":
        host = _MCP_FALLBACK_HOST
    if port is None:
        port = int(os.environ.get("GOLDBERG_MCP_PORT", "8765"))
    url = f"http://{host}:{port}/mcp"
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "goldberg-doctor", "version": "1"},
        },
    }
    try:
        resp = requests.post(
            url,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DOWN,
            detail=f"unreachable at {host}:{port}: {exc}",
            latency_ms=_elapsed_ms(start),
        )
    latency = _elapsed_ms(start)
    if resp.status_code == 200:
        session = resp.headers.get("Mcp-Session-Id")
        detail = f"handshake ok ({host}:{port})"
        if session:
            detail += f", session {session[:8]}"
        return ComponentHealth(
            name=name,
            status=ComponentStatus.UP,
            detail=detail,
            latency_ms=latency,
        )
    return ComponentHealth(
        name=name,
        status=ComponentStatus.DOWN,
        detail=f"HTTP {resp.status_code} at {host}:{port}",
        latency_ms=latency,
    )


def probe_live_index_watcher(
    client: Any | None = None,
    *,
    freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
    events_index: str = EVENTS_INDEX,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> ComponentHealth:
    """Watcher liveness, **inferred** from pipeline-event freshness (C-003, FR-006).

    The watcher runs on a remote host and cannot be probed directly, so we read the
    most-recent pipeline event: newer than the freshness window → UP (inferred),
    otherwise / none / unreadable → DOWN (inferred). ``inferred`` is always set.
    """
    name = "live_index_watcher"
    start = perf_counter()
    if client is None:
        client = _es_client()
    try:
        resp = client.search(
            index=events_index,
            size=1,
            sort=[{"ts": {"order": "desc"}}],
            source_includes=["ts"],
        )
        hits = resp["hits"]["hits"]
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DOWN,
            detail=f"cannot read pipeline events: {exc}",
            latency_ms=_elapsed_ms(start),
            inferred=True,
        )
    latency = _elapsed_ms(start)
    if not hits:
        return ComponentHealth(
            name=name,
            status=ComponentStatus.DOWN,
            detail="no pipeline events; watcher likely paused",
            latency_ms=latency,
            inferred=True,
        )
    age = _age_seconds(hits[0].get("_source", {}).get("ts"))
    if age is not None and age <= freshness_seconds:
        return ComponentHealth(
            name=name,
            status=ComponentStatus.UP,
            detail=f"last event {int(age)}s ago (< {int(freshness_seconds)}s window)",
            latency_ms=latency,
            inferred=True,
        )
    return ComponentHealth(
        name=name,
        status=ComponentStatus.DOWN,
        detail="no recent pipeline events; watcher likely paused",
        latency_ms=latency,
        inferred=True,
    )


# ── orchestration ────────────────────────────────────────────────────────────


def run_doctor(
    *,
    es_client: Any | None = None,
    docling_client: Any | None = None,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    board_timeout: float = DEFAULT_BOARD_TIMEOUT,
    freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
) -> DoctorReport:
    """Run every probe concurrently and assemble the :class:`DoctorReport`.

    Probes share one lazily-built ES client. Each runs on its own thread; any probe
    still running when ``board_timeout`` elapses is reported DOWN ("timed out") so the
    board always returns within budget (NFR-001).
    """
    if es_client is None:
        try:
            es_client = _es_client()
        except Exception:  # noqa: BLE001 - a client-build failure surfaces per-probe
            es_client = None

    # Look up probes via the module namespace so tests can monkeypatch them.
    probes: dict[str, Callable[[], ComponentHealth]] = {
        "elasticsearch": lambda: probe_elasticsearch(es_client, timeout=probe_timeout),
        "docling": lambda: probe_docling(docling_client, timeout=probe_timeout),
        "enricher": lambda: probe_enricher(timeout=probe_timeout),
        "mcp_server": lambda: probe_mcp_server(timeout=probe_timeout),
        "live_index_watcher": lambda: probe_live_index_watcher(
            es_client, freshness_seconds=freshness_seconds, timeout=probe_timeout
        ),
    }

    results: dict[str, ComponentHealth] = {}
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(probes))
    try:
        future_to_name = {executor.submit(fn): name for name, fn in probes.items()}
        done, not_done = concurrent.futures.wait(future_to_name, timeout=board_timeout)
        for future in done:
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as exc:  # noqa: BLE001 - a probe crash is still DOWN
                results[name] = ComponentHealth(
                    name=name,
                    status=ComponentStatus.DOWN,
                    detail=f"probe error: {exc}",
                )
        for future in not_done:
            name = future_to_name[future]
            results[name] = ComponentHealth(
                name=name,
                status=ComponentStatus.DOWN,
                detail=f"probe timed out (> {board_timeout:g}s)",
            )
    finally:
        # Don't block on any thread still winding down (its own request timeout ends it).
        executor.shutdown(wait=False)

    components = [results[name] for name in PROBE_ORDER if name in results]
    overall = worst_status(c.status for c in components)
    return DoctorReport(generated_at=now_iso(), components=components, overall=overall)
