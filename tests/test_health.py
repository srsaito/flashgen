from unittest.mock import patch

from fastapi.testclient import TestClient

import flashgen
from flashgen_mcp.app import app


def test_health() -> None:
    client = TestClient(app)

    with patch.object(flashgen, "anki_invoke", return_value=6):
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
