"""
Shared pytest fixtures for the Can-I-Buy test suite.

conftest.py is loaded automatically by pytest before any test file.
It sets dummy environment variables before app.py is imported so that
the module-level Anthropic client instantiation doesn't require a real key.
"""
import os
import pytest

# ── Dummy keys must be set before `app` is imported ──────────────
# The Anthropic client is created at module level in app.py.
# A missing key won't raise on construction, but set it anyway so
# os.getenv() calls inside helpers also behave predictably.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault("OPENAI_API_KEY",    "test-key-not-real")

from app import app as flask_app  # noqa: E402 — must come after env setup


@pytest.fixture
def client():
    """Flask test client with TESTING mode enabled."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
