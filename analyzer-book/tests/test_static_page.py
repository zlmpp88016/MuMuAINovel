from __future__ import annotations

from fastapi.testclient import TestClient


def test_index_page_and_static_assets_are_served(client: TestClient) -> None:
    index_response = client.get("/")
    assert index_response.status_code == 200
    assert "Book Analyzer" in index_response.text
    assert "/static/app.js" in index_response.text

    script_response = client.get("/static/app.js")
    assert script_response.status_code == 200
    assert "uploadSelectedFile" in script_response.text

    style_response = client.get("/static/styles.css")
    assert style_response.status_code == 200
    assert "upload-panel" in style_response.text
