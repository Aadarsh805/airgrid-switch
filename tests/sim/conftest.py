"""Shared setup for sim tests: repo root importable, UTF-8 reads, temp auth config (Phase 03).

AIRGRID_TOKENS / AIRGRID_HUMANS point at temp files written here, at conftest import time,
so they are in place before any test imports sim.app.
"""

import os
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.sim.helpers import HUMANS_DOC, TOKENS_DOC  # noqa: E402

_AUTH_DIR = Path(tempfile.mkdtemp(prefix="airgrid-test-auth-"))
(_AUTH_DIR / "tokens.yaml").write_text(yaml.safe_dump(TOKENS_DOC), encoding="utf-8")
(_AUTH_DIR / "humans.yaml").write_text(yaml.safe_dump(HUMANS_DOC), encoding="utf-8")
os.environ["AIRGRID_TOKENS"] = str(_AUTH_DIR / "tokens.yaml")
os.environ["AIRGRID_HUMANS"] = str(_AUTH_DIR / "humans.yaml")
