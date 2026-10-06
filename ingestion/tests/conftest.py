"""Run from the repo root: python -m pytest ingestion/tests

ingestion.common puts backend/ on sys.path, so backend modules import as config, integrations, ...
"""

import ingestion.common  # noqa: F401  (side effect: backend on sys.path)
