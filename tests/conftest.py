import shutil
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path):
    """A controlled A/A fixture pair for testing the measuring instrument."""
    root = Path(__file__).resolve().parents[1]
    for directory in ("corpus", "prompts", "fixtures"):
        shutil.copytree(root / directory, tmp_path / directory)
    shutil.copy(root / "uv.lock", tmp_path / "uv.lock")
    # The proposed candidate is allowed to fail CI. Instrument tests always start from a good pair.
    shutil.copy(tmp_path / "fixtures/baseline.json", tmp_path / "fixtures/candidate.json")
    return tmp_path
