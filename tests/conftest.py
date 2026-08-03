"""
Shared pytest fixtures for the Clear2Buy test suite.

conftest.py is loaded automatically by pytest before any test file.
No API keys are required — the app uses a local Ollama instance.
"""
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
