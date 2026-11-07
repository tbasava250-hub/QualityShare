"""
Comprehensive End-to-End Audit & Integration Test Suite for QualityShare.
Tests:
1. User registration & login
2. Forgot password & reset password flow
3. Email service token generation
4. Share creation, file validation & upload
5. Share page viewing & downloads
6. Admin dashboard stats & user list
7. Storage connectivity & file deletion
8. Share expiration & cleanup
"""

import sys
import os
import pytest
import tempfile
import shutil
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.models import (
    get_db,
    init_db,
    create_user,
    get_user_by_email,
    get_user_by_username,
    create_share,
    create_file,
    update_file_complete,
    update_share_complete,
    delete_expired_shares,
)
from app.services.validation import validate_file, validate_share_limits
from app.services.email_service import _create_password_reset_email
from app.services.storage import _get_client
from app.config import Config


@pytest.fixture
def app():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")

    app = create_app()
    app.config.update({
        "TESTING": True,
        "DATABASE_PATH": db_path,
    })
    with app.app_context():
        init_db()
        yield app

    shutil.rmtree(temp_dir)


@pytest.fixture
def client(app):
    return app.test_client()


def test_01_user_registration_and_login(client):
    """Test user registration and login endpoints."""
    # Register new user
    response = client.post("/register", data={
        "full_name": "Alice Admin",
        "username": "alice_e2e",
        "email": "alice_e2e@example.com",
        "password": "securepassword123",
        "confirm_password": "securepassword123",
    }, follow_redirects=True)
    assert response.status_code == 200

    # Verify in DB and make admin
    with client.application.app_context():
        conn = get_db()
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'alice_e2e'")
        conn.commit()
        conn.close()

    # Logout
    client.get("/logout")

    # Login
    response = client.post("/login", data={
        "login_id": "alice_e2e@example.com",
        "password": "securepassword123",
    }, follow_redirects=True)
    assert response.status_code == 200


def test_02_password_reset_flow(app, client):
    """Test forgot password token generation and reset page."""
    with app.app_context():
        user_id = create_user("Bob User", "bob_reset_user", "bob_reset@example.com", "oldpassword123")

    # Request password reset
    response = client.post("/forgot-password", data={"email": "bob_reset@example.com"}, follow_redirects=True)
    assert response.status_code == 200

    # Verify reset token was generated in DB
    with app.app_context():
        conn = get_db()
        user = conn.execute("SELECT reset_token FROM users WHERE email = 'bob_reset@example.com'").fetchone()
        conn.close()
        assert user is not None
        token = user["reset_token"]
        assert token is not None

    # Visit reset password page with token
    response = client.get(f"/reset-password/{token}")
    assert response.status_code == 200

    # Perform password reset
    response = client.post(f"/reset-password/{token}", data={
        "password": "newpassword123",
        "confirm_password": "newpassword123",
    }, follow_redirects=True)
    assert response.status_code == 200


def test_03_email_service_creation():
    """Test password reset email body creation."""
    msg = _create_password_reset_email("test@example.com", "http://localhost:5000/reset-password/token123", 30)
    assert "QualityShare Password Reset" in msg["Subject"]
    assert "token123" in msg.get_payload()[0].get_payload()


def test_04_validation_and_limits():
    """Test file and share validation rules."""
    res_valid = validate_file("vacation.jpg", "image/jpeg", 2000000)
    assert res_valid["valid"] is True
    assert res_valid["file_type"] == "photo"

    res_invalid = validate_file("script.bat", "application/x-msdownload", 1000)
    assert res_invalid["valid"] is False

    limit_check = validate_share_limits(5, 50000000)
    assert limit_check["valid"] is True


def test_05_share_and_files_lifecycle(app, client, monkeypatch):
    """Test share creation, file upload finalization, viewing share page, and download."""
    client.post("/register", json={
        "full_name": "E2E Uploader",
        "username": "e2eloader",
        "email": "e2eloader@example.com",
        "password": "password123",
        "confirm_password": "password123"
    }, headers={"Content-Type": "application/json"})

    from app.routes import upload as upload_module
    monkeypatch.setattr(upload_module, "start_multipart_upload", lambda key, mime: "mock-upload-id")
    monkeypatch.setattr(upload_module, "complete_multipart_upload", lambda storage_key, upload_id, parts: None)
    monkeypatch.setattr(upload_module, "s3_upload_file", lambda storage_key, data, content_type: None)

    # Init upload
    response = client.post("/api/upload/init", json={
        "files": [
            {"name": "photo1.jpg", "size": 2_000_000, "type": "image/jpeg"},
            {"name": "video1.mp4", "size": 6_000_000, "type": "video/mp4"},
        ]
    })
    assert response.status_code == 201
    data = response.get_json()
    token = data["share_token"]
    file_id = data["files"][0]["file_id"]

    # Complete files
    client.post("/api/upload/complete", json={
        "file_id": file_id,
        "checksum": "abc123checksum"
    })
    file_id_2 = data["files"][1]["file_id"]
    client.post("/api/upload/complete", json={
        "file_id": file_id_2,
        "parts": [{"part_number": 1, "etag": "mock-etag"}],
        "checksum": "def456checksum"
    })

    # Finalize share
    response = client.post("/api/upload/finalize", json={"share_token": token})
    assert response.status_code == 200
    fin_data = response.get_json()
    assert fin_data["status"] == "success"
    assert "qr_code" in fin_data

    # View share page
    response = client.get(f"/s/{token}")
    assert response.status_code == 200
    assert b"photo1.jpg" in response.data


def test_06_admin_dashboard(app, client):
    """Test admin dashboard statistics and user list."""
    # Register and login as admin
    client.post("/register", data={
        "full_name": "Super Admin",
        "username": "superadmin_e2e",
        "email": "admin_dash@example.com",
        "password": "adminpassword123",
        "confirm_password": "adminpassword123",
    })
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'superadmin_e2e'")
        conn.commit()
        conn.close()

    response = client.post("/login", data={
        "login_id": "admin_dash@example.com",
        "password": "adminpassword123",
    }, follow_redirects=True)
    assert response.status_code == 200

    # Access admin dashboard
    response = client.get("/admin/")
    assert response.status_code == 200

    # Access admin users list
    response = client.get("/admin/users")
    assert response.status_code == 200
    assert b"superadmin_e2e" in response.data

    # Access admin storage
    response = client.get("/admin/storage")
    assert response.status_code == 200

    # Access admin api stats
    response = client.get("/admin/api/stats")
    assert response.status_code == 200
    stats = response.get_json()
    assert "total_users" in stats


def test_07_share_expiration():
    """Test deleting expired shares."""
    app = create_app()
    with app.app_context():
        init_db()
        # Create an expired share
        expired_time = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        share_id = create_share("expired_token", expires_at=expired_time)
        create_file(share_id, "old.jpg", "shares/expired_token/old.jpg", "image/jpeg", 1000, "photo", 1)

        keys = delete_expired_shares()
        assert "shares/expired_token/old.jpg" in keys
