"""
Shared pytest fixtures for the Clear2Buy test suite.

conftest.py is loaded automatically by pytest before any test file.
No API keys are required — the app uses a local Ollama instance.
"""
import os
import shutil
import tempfile

# Must run before `import app`, since usage_db resolves its DB path from
# DATA_DIR at import time. Without this, running the test suite writes
# real rows into the production usage database.
_OWNS_DATA_DIR = "DATA_DIR" not in os.environ
if _OWNS_DATA_DIR:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="c2b_test_")
_TEST_DATA_DIR = os.environ["DATA_DIR"]

import pytest

from app import app as flask_app


@pytest.fixture
def client():
    """Flask test client with TESTING mode enabled and a logged-in session."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_email"] = "rjames.callahan@gmail.com"
        yield c


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    """Delete the isolated test database once the whole session finishes."""
    yield
    if _OWNS_DATA_DIR:
        shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
