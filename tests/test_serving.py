"""One origin, as on Cloud Run: the API under /api, the built front end on every other path (#13)."""

import pytest
from fastapi.testclient import TestClient

from vouch.api import create_app

PAGE = "<!doctype html><title>Vouch</title>"
SCRIPT = "console.log('vouch')"
BROWSER = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}  # a page load or a refresh


@pytest.fixture
def client(tmp_path):
    """The app serving a stand-in for frontend/dist."""
    (tmp_path / "index.html").write_text(PAGE)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text(SCRIPT)
    return TestClient(create_app(frontend=tmp_path))


def test_the_root_serves_the_landing_page(client):
    response = client.get("/", headers=BROWSER)

    assert response.status_code == 200
    assert response.text == PAGE


def test_a_built_asset_is_served_as_itself(client):
    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.text == SCRIPT


def test_a_deep_link_loads_the_page_so_the_app_can_route_it(client):
    response = client.get("/controls/NK-ENC-01", headers=BROWSER)

    assert response.status_code == 200
    assert response.text == PAGE


def test_the_api_still_answers_beside_the_front_end(client):
    response = client.get("/api/health", headers=BROWSER)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_an_unknown_api_path_is_a_404_not_the_page(client):
    response = client.get("/api/nothing-here", headers=BROWSER)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_a_missing_asset_is_a_404_not_the_page(client):
    response = client.get("/assets/gone.js", headers={"accept": "*/*"})

    assert response.status_code == 404


def test_without_a_build_the_app_refuses_to_start_and_says_why(tmp_path):
    missing = tmp_path / "dist"

    with pytest.raises(SystemExit) as stopped:
        create_app(frontend=missing)

    message = str(stopped.value.code)
    assert str(missing) in message
    assert "npm run build" in message
