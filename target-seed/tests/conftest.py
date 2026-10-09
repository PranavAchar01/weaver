import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["LEDGERLINE_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.setdefault("SECRET_KEY", "test-only-key")

import pytest  # noqa: E402


@pytest.fixture()
def client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()
