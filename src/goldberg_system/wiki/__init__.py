"""SilverBullet concept-wiki output (M11, ADR 0007).

The wiki is a second representation of the corpus, built strictly *downstream of
enrichment*: pages are synthesised from the enriched documents (entities, attributed
claims, summaries), never from raw. This package holds the page renderers (pure) and,
later, the author sink that consumes ``EnrichedDocument`` like the ES indexer does.
"""
