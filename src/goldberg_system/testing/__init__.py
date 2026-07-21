"""Hard-case testing (M8).

An isolated regression suite for the extraction/ingestion pipeline, run against a
separate ES index so it never touches the live corpus. Two kinds of case:

- **real** — actual corpus documents that failed or misbehaved. WORKFLOW: whenever a
  document causes trouble, add it to ``config/hard-cases.yaml`` so we never regress.
- **synthetic** — documents we generate to probe theoretical limits (huge text, empty
  PDF, odd encodings), to test what breaks *before* a real one does.
"""
