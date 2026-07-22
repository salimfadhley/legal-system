"""Command-line entry point for goldberg-system.

The ``search`` / ``claims`` / ``wiki`` / ``get`` / ``facets`` commands are the corpus
query layer: an agent (Claude Code) runs them to gather grounded, citable evidence
and then synthesises an attributed answer. ``search``/``claims`` query the evidence
documents; ``wiki`` queries the synthesised concept wiki (ADR 0007) — a second
representation. See ``AGENTS.md``.
"""

from __future__ import annotations

import json

import click

from goldberg_system import __version__
from goldberg_system.config import load_projects


@click.group()
@click.version_option(__version__)
def main() -> None:
    """goldberg-system: the Goldberg document analysis pipeline."""


@main.command()
def config() -> None:
    """Print the resolved project and service locations (from config/projects.yaml)."""
    cfg = load_projects()
    click.echo("Projects:")
    for name, entry in cfg.get("projects", {}).items():
        click.echo(f"  {name:10s} {entry['path']}")
    archive = cfg.get("archive", {})
    if archive:
        click.echo(f"\nFrozen archive:\n  {archive.get('path')}")
    halob = cfg.get("halob", {})
    if halob:
        click.echo("\nHalob services:")
        click.echo(
            f"  elasticsearch  {halob.get('elasticsearch')}  (index: {halob.get('elasticsearch_index')})"
        )
        click.echo(f"  nats           {halob.get('nats')}")


def _query():  # type: ignore[no-untyped-def]
    from goldberg_system.query import CorpusQuery

    return CorpusQuery.from_env()


@main.command()
@click.argument("text")
@click.option(
    "--matter", "matters", multiple=True, help="Filter by matter (repeatable)."
)
@click.option("--author", default=None, help="Filter by author/speaker.")
@click.option("--type", "document_type", default=None, help="Filter by document_type.")
@click.option("--size", default=10, show_default=True, help="Max hits.")
def search(text, matters, author, document_type, size) -> None:  # type: ignore[no-untyped-def]
    """Full-text search the corpus (BM25) with optional filters."""
    hits = _query().search(
        text,
        matters=list(matters) or None,
        author=author,
        document_type=document_type,
        size=size,
    )
    if not hits:
        click.echo("(no matches)")
        return
    for h in hits:
        click.echo(f"\n• {h.doc_id}  [{h.document_type or '?'}]  score={h.score:.2f}")
        click.echo(f"  raw_path: {h.raw_path}")
        click.echo(
            f"  matters: {', '.join(h.matters) or '-'}  | author: {h.author or '-'}"
        )
        if h.summary:
            click.echo(f"  summary: {h.summary}")
        for frag in h.highlights:
            click.echo(f"  … {frag.strip()} …")


@main.command()
@click.argument("text")
@click.option(
    "--layer", default=None, help="Filter by layer (entity/concept/comparison/…)."
)
@click.option("--tag", "tags", multiple=True, help="Filter by tag (repeatable).")
@click.option("--size", default=10, show_default=True, help="Max pages.")
def wiki(text, layer, tags, size) -> None:  # type: ignore[no-untyped-def]
    """Search the SilverBullet concept wiki — the synthesised, cross-linked view.

    A second representation of the corpus (ADR 0007): curated concept/entity pages.
    Search this alongside `search`/`claims` to find synthesised context the raw
    documents don't state in one place.
    """
    pages = _query().wiki(text, layer=layer, tags=list(tags) or None, size=size)
    if not pages:
        click.echo("(no wiki pages)")
        return
    for p in pages:
        click.echo(f"\n• {p.title or p.path}  [{p.layer or '?'}]  score={p.score:.2f}")
        click.echo(f"  page: {p.path}")
        if p.tags:
            click.echo(f"  tags: {', '.join(p.tags)}")
        if p.sources:
            click.echo(f"  sources: {', '.join(p.sources[:4])}")
        if p.outbound_links:
            click.echo(
                f"  links: {', '.join('[[' + link + ']]' for link in p.outbound_links[:6])}"
            )
        for frag in p.highlights:
            click.echo(f"  … {frag.strip()} …")


@main.command()
@click.option(
    "--by", "asserted_by", default=None, help="Claims asserted by this speaker."
)
@click.option("--subject", default=None, help="Claims about this subject.")
@click.option("--object", "object_", default=None, help="Claims with this object.")
@click.option("--text", default=None, help="Free-text over subject/predicate/object.")
@click.option(
    "--matter", "matters", multiple=True, help="Filter by matter (repeatable)."
)
@click.option("--size", default=20, show_default=True, help="Max documents.")
def claims(asserted_by, subject, object_, text, matters, size) -> None:  # type: ignore[no-untyped-def]
    """Search attributed claims — who asserted what about whom."""
    results = _query().claims(
        asserted_by=asserted_by,
        subject=subject,
        object=object_,
        text=text,
        matters=list(matters) or None,
        size=size,
    )
    if not results:
        click.echo("(no claims)")
        return
    for c in results:
        who = c.asserted_by or "?"
        click.echo(f"\n• {who} asserts: {c.subject} — {c.predicate} — {c.object}")
        click.echo(f"  source: {c.doc_id}  ({c.raw_path})")


@main.command()
@click.argument("doc_id")
@click.option(
    "--content/--no-content", default=True, help="Include the full extracted text."
)
def get(doc_id, content) -> None:  # type: ignore[no-untyped-def]
    """Fetch a document's metadata (and content) by id."""
    doc = _query().get(doc_id)
    if doc is None:
        click.echo(f"(not found: {doc_id})")
        raise SystemExit(1)
    if not content:
        doc.pop("content", None)
    click.echo(json.dumps(doc, indent=2, ensure_ascii=False))


@main.command("test-hard-cases")
@click.option("--only", multiple=True, help="Run only these case names/raw_paths.")
def test_hard_cases(only) -> None:  # type: ignore[no-untyped-def]
    """Run the extraction hard-case regression suite (config/hard-cases.yaml).

    Isolated: extracts each known-hard document (real + synthetic) via Docling and
    checks it against its expectation. No live index touched, no OpenAI cost. Exits
    non-zero on any failure. Add every troublesome document to the registry.
    """
    import tempfile

    from goldberg_system.config import project_path
    from goldberg_system.extract.docling_client import DoclingClient
    from goldberg_system.testing.hard_cases import run_hard_cases

    docling = DoclingClient.from_env()
    if not docling.health():
        raise SystemExit(
            f"docling not reachable at {docling.base_url} (start the tunnel)."
        )
    with tempfile.TemporaryDirectory() as work:
        results = run_hard_cases(
            docling, project_path("raw"), work, only=set(only) or None
        )
    failed = [r for r in results if not r.ok]
    for r in results:
        click.echo(f"  {'✓' if r.ok else '✗'} [{r.kind}] {r.name}  — {r.detail}")
    click.echo(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        raise SystemExit(1)


@main.command()
def facets() -> None:
    """Show corpus facets (matters, authors, document types, parties)."""
    for field, buckets in _query().facets().items():
        click.echo(f"\n{field}:")
        for key, count in buckets:
            click.echo(f"  {count:5d}  {key}")


@main.command()
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    help="Provenance manifest (JSON) = the expected set.",
)
@click.option(
    "--missing", "show_missing", is_flag=True, help="List every missing raw_path."
)
@click.option(
    "--extra",
    "show_extra",
    is_flag=True,
    help="List indexed raw_paths not in the manifest.",
)
def audit(manifest_path, show_missing, show_extra) -> None:  # type: ignore[no-untyped-def]
    """Reconcile the corpus: expected (manifest) vs actual (index) → what did not ingest.

    Completeness is a correctness property for a legal corpus (ADR 0008): a document
    that never ingested is an invisible hole. This joins on raw_path and reports the
    gap.
    """
    from goldberg_system.observability.reconcile import Reconciler

    q = _query()
    report = Reconciler(q.client, q.index).run(manifest_path)
    status = "✓ COMPLETE" if report.complete else "✗ GAPS FOUND"
    click.echo(f"{status}")
    click.echo(f"  expected (manifest): {report.expected_count}")
    click.echo(f"  indexed (actual):    {report.actual_count}")
    click.echo(f"  matched:             {len(report.matched)}")
    click.echo(f"  MISSING (not ingested): {len(report.missing)}")
    click.echo(f"  extra (no manifest entry): {len(report.extra)}")
    if report.missing_by_matter:
        click.echo("  missing by matter:")
        for matter, n in report.missing_by_matter.items():
            click.echo(f"    {n:5d}  {matter}")
    if show_missing:
        click.echo("\nMissing raw_paths:")
        for rp in report.missing:
            click.echo(f"  - {rp}")
    if show_extra:
        click.echo("\nExtra (indexed, not in manifest):")
        for rp in report.extra:
            click.echo(f"  - {rp}")
    if not report.complete:
        raise SystemExit(1)


@main.command()
@click.option(
    "--manifest",
    "manifest_path",
    default=None,
    help="Also check completeness against this provenance manifest.",
)
@click.option(
    "--max-failures",
    default=0,
    show_default=True,
    help="Allowed pipeline failures before alerting.",
)
@click.option(
    "--alert-on-skipped", is_flag=True, help="Also alert on skipped documents."
)
@click.option("--json", "as_json", is_flag=True, help="Emit alerts as JSON.")
def alert(manifest_path, max_failures, alert_on_skipped, as_json) -> None:  # type: ignore[no-untyped-def]
    """Proactive check — exits non-zero when the corpus has gaps or failures (M12).

    Drive it from a scheduler (cron on Halob / Jenkins) so silent drops surface
    without anyone running `goldberg audit`. Exit 2 = critical, 1 = warning, 0 = clear.
    """
    from goldberg_system.observability.alerts import evaluate_alerts, exit_code
    from goldberg_system.observability.reconcile import Reconciler
    from goldberg_system.observability.state import aggregate

    q = _query()
    state = aggregate(q.client)
    recon = Reconciler(q.client, q.index).run(manifest_path) if manifest_path else None
    alerts = evaluate_alerts(
        state, recon, max_failures=max_failures, alert_on_skipped=alert_on_skipped
    )
    if as_json:
        click.echo(json.dumps([a.model_dump() for a in alerts], indent=2))
    elif not alerts:
        click.echo("✓ all clear — no gaps or failures")
    else:
        for a in alerts:
            mark = "✗" if a.level == "critical" else "⚠"
            click.echo(f"{mark} [{a.level}] {a.code}: {a.message}")
    raise SystemExit(exit_code(alerts))


@main.command()
@click.option(
    "--yaml", "as_yaml", is_flag=True, help="Emit the LLM-readable YAML mode."
)
def status(as_yaml) -> None:  # type: ignore[no-untyped-def]
    """System state — health, corpus, wiki, pipeline, DLQ (M12/M13, ADR 0009).

    Human table by default; --yaml emits the same canonical SystemState as YAML so an
    LLM can grok the whole system in one read.
    """
    from goldberg_system.observability.state import aggregate

    state = aggregate(_query().client)
    if as_yaml:
        click.echo(state.to_yaml())
        return
    h = state.health
    click.echo(f"health: {h['status'].upper()}")
    for c in h["checks"]:
        click.echo(f"  {'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}")
    click.echo(f"\ncorpus: {state.corpus['documents']} documents")
    for matter, n in list(state.corpus["by_matter"].items())[:8]:
        click.echo(f"    {n:5d}  {matter}")
    click.echo(f"wiki: {state.wiki['pages']} pages  {state.wiki['by_layer']}")
    click.echo(f"\npipeline: last indexed {state.pipeline['last_indexed_at'] or '-'}")
    for k, n in sorted(state.pipeline["by_stage_status"].items()):
        click.echo(f"    {n:5d}  {k}")
    click.echo(f"\ndlq: {state.dlq['failed']} failed, {state.dlq['skipped']} skipped")
    for e in state.dlq["recent"][:5]:
        click.echo(
            f"    {e.get('status')}  {e.get('stage')}  {e.get('raw_path') or e.get('doc_id')}  — {e.get('reason') or ''}"
        )


@main.command("dlq")
@click.option(
    "--status",
    "statuses",
    multiple=True,
    default=("failed",),
    help="Event statuses to list (default: failed). Use --status skipped too.",
)
@click.option("--size", default=25, show_default=True, help="Max entries.")
def dlq(statuses, size) -> None:  # type: ignore[no-untyped-def]
    """List failed/skipped documents — the ES-projection dead-letter view (M12).

    (The durable NATS JetStream DLQ with retry arrives with the NATS increment; this
    reads the same failure signal from the event projection.)
    """
    from goldberg_system.observability.state import _recent

    q = _query()
    entries = _recent(q.client, "goldberg_pipeline_events", list(statuses), size=size)
    if not entries:
        click.echo(f"(no {'/'.join(statuses)} events)")
        return
    for e in entries:
        click.echo(f"\n• {e.get('status')}/{e.get('stage')}  {e.get('ts')}")
        click.echo(f"  {e.get('raw_path') or e.get('doc_id')}")
        if e.get("reason"):
            click.echo(f"  reason: {e['reason']}")


@main.command("mcp-serve")
@click.option("--host", default=None, help="Bind host (default 0.0.0.0 / env).")
@click.option("--port", default=None, type=int, help="Bind port (default 8765 / env).")
def mcp_serve(host, port) -> None:  # type: ignore[no-untyped-def]
    """Run the hosted MCP server (M14) — LLM visibility + query. Needs the `mcp` extra."""
    import os

    if host:
        os.environ["GOLDBERG_MCP_HOST"] = host
    if port:
        os.environ["GOLDBERG_MCP_PORT"] = str(port)
    try:
        from goldberg_system.mcp.server import main as serve
    except ModuleNotFoundError:
        raise SystemExit("mcp not installed — run `uv sync --extra mcp` first.")
    serve()


@main.command()
@click.option("--port", default=8501, show_default=True, help="Port to serve on.")
def dashboard(port) -> None:  # type: ignore[no-untyped-def]
    """Launch the Streamlit operations dashboard (M13). Needs the `dashboard` extra."""
    import subprocess
    from pathlib import Path

    app = Path(__file__).parent / "dashboard" / "app.py"
    try:
        subprocess.run(
            ["streamlit", "run", str(app), "--server.port", str(port)], check=True
        )
    except FileNotFoundError:
        raise SystemExit(
            "streamlit not installed — run `uv sync --extra dashboard` first."
        )


@main.command()
@click.argument("key")
def trace(key) -> None:  # type: ignore[no-untyped-def]
    """Show one document's pipeline timeline — why did X (not) ingest.

    KEY matches a doc_id, raw_path, or sha256 (M12 / ADR 0008).
    """
    from goldberg_system.observability.trace import read_trace

    events = read_trace(_query().client, key)
    if not events:
        click.echo(f"(no pipeline events for {key})")
        return
    for e in events:
        mark = {"ok": "✓", "skipped": "⊘", "failed": "✗", "started": "…"}.get(
            e.status, "?"
        )
        line = f"  {e.ts}  {mark} {e.stage}/{e.status}"
        if e.reason:
            line += f"  — {e.reason}"
        click.echo(line)
        if e.error:
            click.echo(f"       error: {e.error}")


@main.command()
@click.option(
    "--max", "max_docs", type=int, default=None, help="Limit documents processed."
)
@click.option(
    "--extracted-root",
    default=None,
    help="Also write frontmatter .md files under this root.",
)
@click.option(
    "--manifest",
    "manifest_path",
    default=None,
    help="Provenance manifest (JSON) to attach real raw_path/matters by SHA-256.",
)
@click.option(
    "--events/--no-events",
    default=True,
    help="Emit pipeline audit events to goldberg_pipeline_events (M12).",
)
def reindex(max_docs, extracted_root, manifest_path, events) -> None:  # type: ignore[no-untyped-def]
    """Backfill the ES index from documents already in Papra (enrich + index)."""
    from goldberg_system.enrichment import OpenAIEnricher
    from goldberg_system.migrate.manifest import Manifest
    from goldberg_system.observability.events import ElasticsearchEventSink
    from goldberg_system.papra import PapraClient
    from goldberg_system.pipeline import backfill_from_papra
    from goldberg_system.provenance import now_iso
    from goldberg_system.sinks import ElasticsearchIndexer, ExtractedRepoWriter

    papra = PapraClient.from_env()
    enricher = OpenAIEnricher.from_settings()
    indexer = ElasticsearchIndexer.from_env()
    indexer.ensure_index()
    sinks: list = [indexer]
    if extracted_root:
        sinks.append(ExtractedRepoWriter(extracted_root))
    manifest = Manifest.load(manifest_path) if manifest_path else None
    if manifest is not None:
        click.echo(f"Using provenance manifest ({len(manifest)} entries)")
    event_sink = None
    run_id = None
    if events:
        event_sink = ElasticsearchEventSink.from_env()
        event_sink.ensure_index()
        run_id = f"reindex-{now_iso()}"
        click.echo(f"Emitting audit events (run {run_id}) → {event_sink.index}")

    def on_doc(stub, status) -> None:  # type: ignore[no-untyped-def]
        click.echo(f"  [{status}] {stub.original_name or stub.id}")

    click.echo(f"Backfilling into {indexer.index} …")
    report = backfill_from_papra(
        papra,
        enricher,
        sinks,
        max_docs=max_docs,
        manifest=manifest,
        events=event_sink,
        run_id=run_id,
        on_doc=on_doc,
    )
    click.echo(f"  with real provenance: {report.with_provenance}")
    click.echo(
        f"\nprocessed={report.processed} indexed={report.indexed} "
        f"skipped_empty={report.skipped_empty} failures={report.failures}"
    )


@main.command()
@click.option(
    "--interval",
    default=300,
    show_default=True,
    help="Seconds between reconcile cycles.",
)
@click.option(
    "--workers",
    default=2,
    show_default=True,
    help="Concurrent documents per cycle (conservative — Halob is 4-core).",
)
@click.option(
    "--batch",
    default=50,
    show_default=True,
    help="Max documents ingested per cycle (bounds CPU per cycle).",
)
@click.option(
    "--once", is_flag=True, help="Run exactly one cycle and exit (testing / cron)."
)
@click.option(
    "--index",
    "index_override",
    default=None,
    help="Target index (e.g. goldberg_documents_test for isolated testing).",
)
@click.option(
    "--health-port",
    default=8080,
    show_default=True,
    help="Port for GET /health (0 to disable). Ignored with --once.",
)
@click.option(
    "--manifest",
    "manifest_path",
    default=None,
    help="Provenance manifest (default: config/provenance-manifest.json).",
)
def watch(interval, workers, batch, once, index_override, health_port, manifest_path) -> None:  # type: ignore[no-untyped-def]
    """Auto-ingestion reconciler — poll goldberg-raw and ingest new files (M15).

    The canonical automatic ingest path (ADR 0011, supersedes the Papra webhook):
    each cycle registers provenance for new files BEFORE indexing, then extracts →
    enriches → indexes the difference via the direct-Docling reingest path, emitting
    pipeline events. Text/passthrough files flow even when Docling is down; OCR files
    dead-letter and retry. Runs forever unless --once.
    """
    import threading

    from goldberg_system.config import project_path
    from goldberg_system.enrichment import OpenAIEnricher
    from goldberg_system.extract.docling_client import DoclingClient
    from goldberg_system.migrate.allowlist import Allowlist
    from goldberg_system.observability.events import ElasticsearchEventSink
    from goldberg_system.reconcile import Reconciler, make_health_server
    from goldberg_system.sinks import ElasticsearchIndexer

    raw_root = project_path("raw")
    mpath = manifest_path or (
        project_path("system") / "config" / "provenance-manifest.json"
    )
    allowlist = Allowlist.load()

    # Do NOT hard-gate on Docling health: the reconciler degrades gracefully — text /
    # passthrough files ingest without Docling; OCR files dead-letter and retry.
    docling = DoclingClient.from_env()
    if not docling.health():
        click.echo(
            f"⚠ docling not reachable at {docling.base_url} — text/passthrough files "
            "will still ingest; OCR files will dead-letter and retry next cycle."
        )

    enricher = OpenAIEnricher.from_settings()
    indexer = ElasticsearchIndexer.from_env()
    if index_override:
        indexer.index = index_override
    indexer.ensure_index()
    event_sink = ElasticsearchEventSink.from_env()
    event_sink.ensure_index()

    def already_indexed() -> set:  # type: ignore[type-arg]
        resp = indexer.client.search(
            index=indexer.index,
            query={"exists": {"field": "raw_sha256"}},
            size=10000,
            source_includes=["raw_sha256"],
        )
        return {
            h["_source"]["raw_sha256"]
            for h in resp["hits"]["hits"]
            if h["_source"].get("raw_sha256")
        }

    reconciler = Reconciler(
        raw_root=raw_root,
        manifest_path=mpath,
        allowlist=allowlist,
        docling=docling,
        enricher=enricher,
        sinks=[indexer],
        already_indexed=already_indexed,
        events=event_sink,
        batch=batch,
        workers=workers,
    )

    def summary(cycle) -> None:  # type: ignore[no-untyped-def]
        click.echo(cycle.summary_line())

    if once:
        summary(reconciler.run_cycle())
        return

    if health_port:
        server = make_health_server(reconciler, port=health_port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        click.echo(f"health endpoint: GET :{health_port}/health")
    click.echo(
        f"reconciler polling {raw_root} every {interval}s "
        f"(batch={batch}, workers={workers}) → {indexer.index}"
    )
    reconciler.run_forever(interval, on_cycle=summary)


@main.group()
def migrate() -> None:
    """Corpus migration (M8): populate goldberg-raw and build the provenance manifest."""


@migrate.command("populate-raw")
@click.option(
    "--dry-run", is_flag=True, help="Report what would be copied without writing."
)
def migrate_populate_raw(dry_run) -> None:  # type: ignore[no-untyped-def]
    """Copy the allowlisted evidence trees from the frozen archive into goldberg-raw."""
    from goldberg_system.config import load_projects, project_path
    from goldberg_system.migrate.allowlist import Allowlist
    from goldberg_system.migrate.populate_raw import populate_raw

    cfg = load_projects()
    archive_root = cfg["archive"]["path"]
    raw_root = project_path("raw")
    allowlist = Allowlist.load()
    click.echo(
        f"{'DRY-RUN: ' if dry_run else ''}populating {raw_root}\n  from {archive_root}\n"
        f"  trees: {', '.join(sorted(allowlist.included))}"
    )
    report = populate_raw(archive_root, raw_root, allowlist, dry_run=dry_run)
    for tree, n in sorted(report.trees.items()):
        click.echo(f"  {tree:22} {n:>6} files")
    mb = report.bytes_copied / (1024 * 1024)
    click.echo(
        f"\n{'would copy' if dry_run else 'copied'} {report.files_copied} files "
        f"({mb:.1f} MB); skipped {report.skipped_excluded} excluded"
    )
    if not dry_run:
        click.echo(
            "Next: cd goldberg-raw && git lfs install && git add -A && git commit"
        )


@migrate.command("manifest")
@click.option("--out", "out_path", default=None, help="Manifest output path (JSON).")
@click.option(
    "--no-commit", is_flag=True, help="Skip per-file git commit lookup (faster)."
)
def migrate_manifest(out_path, no_commit) -> None:  # type: ignore[no-untyped-def]
    """Build the SHA-256 provenance manifest by walking goldberg-raw."""
    from goldberg_system.config import project_path
    from goldberg_system.migrate.allowlist import Allowlist
    from goldberg_system.migrate.manifest import build_manifest, write_manifest

    raw_root = project_path("raw")
    dest = out_path or (project_path("system") / "config" / "provenance-manifest.json")
    entries = build_manifest(raw_root, Allowlist.load(), with_commit=not no_commit)
    write_manifest(entries, dest)
    with_matter = sum(1 for e in entries if e.matters)
    click.echo(
        f"manifest: {len(entries)} files → {dest}\n"
        f"  with matter: {with_matter}  | without: {len(entries) - with_matter}"
    )


@migrate.command("reingest")
@click.option(
    "--manifest",
    "manifest_path",
    default=None,
    help="Provenance manifest (default: config/provenance-manifest.json).",
)
@click.option(
    "--max",
    "max_docs",
    type=int,
    default=None,
    help="Cap documents (for test injections).",
)
@click.option(
    "--only",
    multiple=True,
    help="Only these raw_paths (repeatable; for targeted tests).",
)
@click.option("--events/--no-events", default=True, help="Emit pipeline audit events.")
@click.option(
    "--index",
    "index_override",
    default=None,
    help="Target index (e.g. goldberg_documents_test for isolated testing).",
)
@click.option("--workers", default=1, show_default=True, help="Concurrent documents.")
@click.option(
    "--resume",
    is_flag=True,
    help="Skip documents already in the index (by raw_sha256).",
)
@click.option(
    "--docling-timeout",
    type=int,
    default=None,
    help="Per-doc Docling wait seconds (fail-fast on doomed files).",
)
def migrate_reingest(manifest_path, max_docs, only, events, index_override, workers, resume, docling_timeout) -> None:  # type: ignore[no-untyped-def]
    """Bulk extract→enrich→index from goldberg-raw via docling-serve DIRECTLY (M8 fix).

    Bypasses Papra's broken extraction. Use --max/--only for test injections and
    --index for an isolated test index, then run unbounded to re-ingest everything.
    --resume skips already-indexed docs (restart-safe).
    """
    from goldberg_system.config import project_path
    from goldberg_system.enrichment import OpenAIEnricher
    from goldberg_system.extract.docling_client import DoclingClient
    from goldberg_system.migrate.manifest import Manifest
    from goldberg_system.migrate.reingest import reingest_from_raw
    from goldberg_system.observability.events import ElasticsearchEventSink
    from goldberg_system.provenance import now_iso
    from goldberg_system.sinks import ElasticsearchIndexer

    raw_root = project_path("raw")
    mpath = manifest_path or (
        project_path("system") / "config" / "provenance-manifest.json"
    )
    manifest = Manifest.load(mpath)
    docling = DoclingClient.from_env()
    if docling_timeout:
        docling.max_wait = float(docling_timeout)
    if not docling.health():
        raise SystemExit(
            f"docling not reachable at {docling.base_url} (start the SSH tunnel or set GOLDBERG_DOCLING_URL)."
        )
    enricher = OpenAIEnricher.from_settings()
    indexer = ElasticsearchIndexer.from_env()
    if index_override:
        indexer.index = index_override  # isolated test index
    indexer.ensure_index()

    skip_shas: set[str] = set()
    if resume:
        resp = indexer.client.search(
            index=indexer.index,
            query={"exists": {"field": "raw_sha256"}},
            size=10000,
            source_includes=["raw_sha256"],
        )
        skip_shas = {
            h["_source"]["raw_sha256"]
            for h in resp["hits"]["hits"]
            if h["_source"].get("raw_sha256")
        }
        click.echo(f"  resume: skipping {len(skip_shas)} already-indexed documents")
    event_sink = None
    run_id = None
    if events:
        event_sink = ElasticsearchEventSink.from_env()
        event_sink.ensure_index()
        run_id = f"reingest-{now_iso()}"

    click.echo(
        f"Re-ingesting from {raw_root} via Docling {docling.base_url} → {indexer.index}"
    )
    click.echo(
        f"  manifest: {len(manifest)} entries"
        + (f"  (max {max_docs})" if max_docs else "")
    )

    def on_doc(raw_path, status) -> None:  # type: ignore[no-untyped-def]
        click.echo(f"  [{status}] {raw_path}")

    report = reingest_from_raw(
        raw_root,
        manifest,
        docling,
        enricher,
        [indexer],
        events=event_sink,
        run_id=run_id,
        max_docs=max_docs,
        only=set(only) or None,
        skip_shas=skip_shas or None,
        workers=workers,
        on_doc=on_doc,
    )
    click.echo(
        f"\nprocessed={report.processed} indexed={report.indexed} "
        f"skipped_empty={report.skipped_empty} skipped_media={report.skipped_media} "
        f"skipped_indexed={report.skipped_indexed} "
        f"missing={report.missing_file} failures={report.failures}"
    )


if __name__ == "__main__":
    main()
