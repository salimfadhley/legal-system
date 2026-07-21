"""Command-line entry point for goldberg-system.

The ``search`` / ``claims`` / ``get`` / ``facets`` commands are the corpus query
layer: an agent (Claude Code) runs them to gather grounded, citable evidence and
then synthesises an attributed answer. See ``AGENTS.md``.
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


@main.command()
def facets() -> None:
    """Show corpus facets (matters, authors, document types, parties)."""
    for field, buckets in _query().facets().items():
        click.echo(f"\n{field}:")
        for key, count in buckets:
            click.echo(f"  {count:5d}  {key}")


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
def reindex(max_docs, extracted_root, manifest_path) -> None:  # type: ignore[no-untyped-def]
    """Backfill the ES index from documents already in Papra (enrich + index)."""
    from goldberg_system.enrichment import OpenAIEnricher
    from goldberg_system.migrate.manifest import Manifest
    from goldberg_system.papra import PapraClient
    from goldberg_system.pipeline import backfill_from_papra
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

    def on_doc(stub, status) -> None:  # type: ignore[no-untyped-def]
        click.echo(f"  [{status}] {stub.original_name or stub.id}")

    click.echo(f"Backfilling into {indexer.index} …")
    report = backfill_from_papra(
        papra, enricher, sinks, max_docs=max_docs, manifest=manifest, on_doc=on_doc
    )
    click.echo(f"  with real provenance: {report.with_provenance}")
    click.echo(
        f"\nprocessed={report.processed} indexed={report.indexed} "
        f"skipped_empty={report.skipped_empty} failures={report.failures}"
    )


@main.group()
def migrate() -> None:
    """Corpus migration (M8): populate goldberg-raw and build the provenance manifest."""


@migrate.command("populate-raw")
@click.option("--dry-run", is_flag=True, help="Report what would be copied without writing.")
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
        click.echo("Next: cd goldberg-raw && git lfs install && git add -A && git commit")


@migrate.command("manifest")
@click.option("--out", "out_path", default=None, help="Manifest output path (JSON).")
@click.option("--no-commit", is_flag=True, help="Skip per-file git commit lookup (faster).")
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


if __name__ == "__main__":
    main()
