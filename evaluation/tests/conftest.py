"""Run from the repo root: python -m pytest evaluation/tests

evaluation.common puts backend/ on sys.path, so backend modules import as config, integrations, ...
"""

import evaluation.common  # noqa: F401  (side effect: backend on sys.path)
