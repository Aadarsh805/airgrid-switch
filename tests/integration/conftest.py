"""Phase 05 fixtures: a real uvicorn subprocess with temp tokens, humans and database.

The tokens and humans are the ones tests/sim uses (tests/sim/helpers.py), written to temp files
here so nothing depends on config/tokens.local.yaml or config/humans.yaml.
"""

import os
import tempfile
from pathlib import Path

import httpx
import pytest
import yaml

from tests.integration.harness import SimProcess
from tests.sim.helpers import HUMANS_DOC, TOKENS_DOC

_AUTH_DIR = Path(tempfile.mkdtemp(prefix="airgrid-integration-auth-"))
TOKENS_PATH = _AUTH_DIR / "tokens.yaml"
HUMANS_PATH = _AUTH_DIR / "humans.yaml"
TOKENS_PATH.write_text(yaml.safe_dump(TOKENS_DOC), encoding="utf-8")
HUMANS_PATH.write_text(yaml.safe_dump(HUMANS_DOC), encoding="utf-8")
# In-process World Bot code reads these; same content as the tests/sim temp files.
os.environ.setdefault("AIRGRID_TOKENS", str(TOKENS_PATH))
os.environ.setdefault("AIRGRID_HUMANS", str(HUMANS_PATH))


@pytest.fixture(scope="session")
def sim(tmp_path_factory):
    proc = SimProcess(tmp_path_factory.mktemp("sim"), TOKENS_PATH, HUMANS_PATH)
    proc.start()
    yield proc
    proc.stop()


@pytest.fixture(scope="session")
def c(sim):
    """httpx client on the running sim; same call shape as the TestClient in tests/sim."""
    with httpx.Client(base_url=sim.url, timeout=60) as client:
        yield client
