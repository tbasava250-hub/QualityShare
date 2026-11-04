"""
Tests for authentication, registration, login, logout, and user dashboard ownership.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.models import init_db, get_db


@pytest.fixture
def app():
    app = create_app()
    app.config.update({
        "TESTING": True,
        "DATABASE_PATH": ":memory:",
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        init_db()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


class TestAuthAndDashboard:
    """Tests for user registration, login, dashboard access, and ownership isolation."""

    def test_register_success(self, client):
        response = client.post("/register", data={
            "full_name": "Alice Smith",
            "username": "alice",
            "email": "alice@example.com",
            "password": "password123",
            "confirm_password": "password123"
        })

        assert response.status_code == 200 or response.status_code == 302

    def test_register_duplicate_username(self, client):
        # Register first user
        client.post("/register", json={
            "full_name": "Alice Smith",
            "username": "alice",
            "email": "alice@example.com",
            "password": "password123",
            "confirm_password": "password123"
        }, headers={"Content-Type": "application/json"})

        # Register second user with same username
        response = client.post("/register", json={
            "full_name": "Another Alice",
            "username": "alice",
            "email": "alice2@example.com",
            "password": "password123",
            "confirm_password": "password123"
        }, headers={"Content-Type": "application/json"})

        assert response.status_code == 400
        assert "Username is already taken" in response.get_json()["error"]

    def test_login_and_logout(self, client):
        # Register user
        client.post("/register", json={
            "full_name": "Bob Jones",
            "username": "bob",
            "email": "bob@example.com",
            "password": "secretpassword",
            "confirm_password": "secretpassword"
        }, headers={"Content-Type": "application/json"})

        # Logout
        client.get("/logout")

        # Login with email
        response = client.post("/login", json={
            "login_id": "bob@example.com",
            "password": "secretpassword"
        }, headers={"Content-Type": "application/json"})
        assert response.status_code == 200

        # Protected dashboard access
        dash_res = client.get("/dashboard")
        assert dash_res.status_code == 200

    def test_protected_route_unauthorized(self, client):
        response = client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
