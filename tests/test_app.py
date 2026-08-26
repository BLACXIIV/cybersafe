import pytest
from app import create_app


@pytest.fixture
def app():
    app = create_app()
    app.config.update({"TESTING": True})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_landing_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Learn Cybersecurity" in response.data
    assert b"Earn Your Internet Access" in response.data
    assert b"circuit-bg" in response.data
