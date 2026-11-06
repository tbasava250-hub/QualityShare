"""
Tests for password reset functionality.
"""

import sys
import os
import pytest
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.models import init_db, get_db


@pytest.fixture
def app():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")

    app = create_app()
    app.config.update({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        init_db()
        yield app

    shutil.rmtree(temp_dir)


@pytest.fixture
def client(app):
    return app.test_client()


class TestPasswordReset:
    """Test password reset flow and edge cases."""

    def test_password_reset_flow(self, client, app):
        """Test complete password reset flow: register -> request reset -> reset password -> login with new password."""
        # 1. Register user
        client.post("/register", json={
            "full_name": "Charlie Brown",
            "username": "charlie",
            "email": "charlie@example.com",
            "password": "oldpassword",
            "confirm_password": "oldpassword"
        }, headers={"Content-Type": "application/json"})

        client.get("/logout")

        # 2. Request password reset
        res = client.post("/forgot-password", data={"email": "charlie@example.com"}, follow_redirects=True)
        assert res.status_code == 200
        assert "password reset link has been sent" in res.get_data(as_text=True)

        # Retrieve reset token from DB
        with app.app_context():
            conn = get_db()
            user = conn.execute("SELECT * FROM users WHERE email = 'charlie@example.com'").fetchone()
            token = user["reset_token"]
            conn.close()

        assert token is not None

        # 3. Access reset page with token
        res_page = client.get(f"/reset-password/{token}")
        assert res_page.status_code == 200

        # 4. Submit new password
        res_submit = client.post(f"/reset-password/{token}", data={
            "password": "newsecurepassword",
            "confirm_password": "newsecurepassword"
        }, follow_redirects=True)
        assert res_submit.status_code == 200
        assert "successfully reset" in res_submit.get_data(as_text=True)

        # 5. Login with new password
        login_res = client.post("/login", json={
            "login_id": "charlie@example.com",
            "password": "newsecurepassword"
        }, headers={"Content-Type": "application/json"})
        assert login_res.status_code == 200

    def test_password_reset_invalid_token(self, client):
        """Test that invalid reset token redirects with error message."""
        res = client.get("/reset-password/nonexistenttoken123", follow_redirects=True)
        assert res.status_code == 200
        assert "Invalid or expired" in res.get_data(as_text=True)

    def test_password_reset_expired_token(self, client, app):
        """Test that expired reset tokens are rejected."""
        # Register user
        client.post("/register", json={
            "full_name": "Dave",
            "username": "dave",
            "email": "dave@example.com",
            "password": "password123",
            "confirm_password": "password123"
        }, headers={"Content-Type": "application/json"})

        # Logout to clear session
        client.get("/logout")

        with app.app_context():
            conn = get_db()
            conn.execute("UPDATE users SET reset_token = 'expiredtoken', reset_expires = '2020-01-01 00:00:00' WHERE email = 'dave@example.com'")
            conn.commit()
            conn.close()

        # First access the expired token - should redirect to forgot-password with expired message
        res = client.get("/reset-password/expiredtoken", follow_redirects=False)
        # Should redirect to forgot-password page
        assert res.status_code == 302
        assert "/forgot-password" in res.location

        # Follow the redirect and check for expired message
        follow_res = client.get("/forgot-password", follow_redirects=True)
        assert follow_res.status_code == 200
        assert "expired" in follow_res.get_data(as_text=True)
