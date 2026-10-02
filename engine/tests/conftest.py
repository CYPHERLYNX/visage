import os
import sys

import pytest

# Import the engine package as `engine.<module>` (relative imports need it).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.fixture
def tmp_store(tmp_path):
    return str(tmp_path / "identities.dat")


@pytest.fixture
def tmp_vault(tmp_path):
    return str(tmp_path / "vault.dat")
