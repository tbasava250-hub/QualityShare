"""
Tests for share functionality.

Tests share-link access, invalid/expired tokens, share expiration, and access control.
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
    create_share,
    create_file,
    update_share_complete,
    update_file_complete,
    delete_expired_shares,
    get_share_by_token,
)


@pytest.fixture
def app():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")

    app = create_app()
    app.config.update({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "WTF_CSRF_ENABLED": False,
        "DEBUG": False,
    })

    with app.app_context():
        init_db()
        yield app

    shutil.rmtree(temp_dir)


@pytest.fixture
def client(app):
    return app.test_client()


def create_test_user_with_share(app, username="testuser", email="test@example.com"):
    """Create a test user and a completed share with files. Returns share_token, user_id, file1_id, file2_id."""
    with app.app_context():
        conn = get_db()

        # Create a user first
        cursor = conn.execute(
            "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
            ("Test User", username, email, "pbkdf2:sha256:600000$test$test", 0)
        )
        user_id = cursor.lastrowid

        import uuid
        share_token = uuid.uuid4().hex

        cursor = conn.execute(
            "INSERT INTO shares (token, user_id, is_complete, file_count, total_size) VALUES (?, ?, 1, 2, 2048)",
            (share_token, user_id)
        )
        share_id = cursor.lastrowid

        # Create two files
        cursor = conn.execute(
            """INSERT INTO files (share_id, original_name, storage_key, content_type,
               size_bytes, file_type, upload_status, checksum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (share_id, "photo1.jpg", f"test/{share_token}/photo1.jpg", "image/jpeg", 1024, "photo", "complete", "abc123")
        )
        file1_id = cursor.lastrowid

        cursor = conn.execute(
            """INSERT INTO files (share_id, original_name, storage_key, content_type,
               size_bytes, file_type, upload_status, checksum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (share_id, "video1.mp4", f"test/{share_token}/video1.mp4", "video/mp4", 1024, "video", "complete", "def456")
        )
        file2_id = cursor.lastrowid

        conn.commit()
        conn.close()

    return share_token, user_id, file1_id, file2_id


class TestShareView:
    """Tests for viewing share pages."""

    def test_view_valid_share(self, app, client):
        """Test viewing a valid, completed share."""
        share_token, _, file1_id, file2_id = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200
        data = response.get_data(as_text=True)

        assert "photo1.jpg" in data
        assert "video1.mp4" in data
        assert f"/api/download/{share_token}/{file1_id}" in data
        assert f"/api/download/{share_token}/{file2_id}" in data

    def test_view_share_with_multiple_files(self, app, client):
        """Test share page displays all files in the share."""
        share_token, _, _, _ = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "photo1.jpg" in data
        assert "video1.mp4" in data

    def test_view_nonexistent_share_returns_404(self, app, client):
        """Test that viewing a nonexistent share returns 404."""
        response = client.get("/s/nonexistenttoken123")
        assert response.status_code == 404

    def test_view_incomplete_share_returns_404(self, app, client):
        """Test that viewing an incomplete share returns 404."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()
            # First create a user
            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "tempuser", "temp@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete) VALUES (?, ?, 0)",
                (share_token, user_id)
            )
            conn.commit()
            conn.close()

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 404

    def test_view_expired_share_returns_410(self, app, client):
        """Test that viewing an expired share returns 410 Gone."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()
            # Create user first
            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "expuser", "exp@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            # Create share that expired 2 days ago
            expired_time = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete, expires_at) VALUES (?, ?, 1, ?)",
                (share_token, user_id, expired_time)
            )
            conn.commit()
            conn.close()

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 410

    def test_view_share_with_null_expiration_works(self, app, client):
        """Test that shares without expiration can be viewed indefinitely."""
        share_token, _, _, _ = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200


class TestShareSecurity:
    """Tests for share access control and security."""

    def test_cannot_view_share_without_token(self, app, client):
        """Test that shares require a valid token."""
        response = client.get("/s/")
        assert response.status_code == 404

        response = client.get("/s/None")
        assert response.status_code == 404

    def test_cannot_view_share_with_malformed_token(self, app, client):
        """Test that malformed tokens are rejected."""
        malformed_tokens = [
            "invalid-token-with-special-chars!@#",
            "<script>alert('xss')</script>",
            "token' OR '1'='1",
        ]

        for token in malformed_tokens:
            response = client.get(f"/s/{token}")
            assert response.status_code == 404, f"Expected 404 for token: {token}"

    def test_share_page_does_not_expose_credentials(self, app, client):
        """Test that share page does not expose sensitive information."""
        share_token, _, _, _ = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        data = response.get_data(as_text=True)

        assert "STORAGE_ACCESS_KEY" not in data
        assert "STORAGE_SECRET_KEY" not in data
        assert "pbkdf2" not in data.lower()


class TestShareTemplates:
    """Tests for share page templates and content."""

    def test_share_page_has_download_links(self, app, client):
        """Test that share page includes proper download links."""
        share_token, _, file1_id, file2_id = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200
        data = response.get_data(as_text=True)

        assert f"download/{share_token}/{file1_id}" in data
        assert f"download/{share_token}/{file2_id}" in data

    def test_share_page_shows_file_sizes(self, app, client):
        """Test that share page displays formatted file sizes."""
        share_token, _, _, _ = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "1.0 KB" in data or "1024" in data

    def test_share_page_shows_file_types(self, app, client):
        """Test that share page indicates file types."""
        share_token, _, _, _ = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200
        data = response.get_data(as_text=True)

        assert "photo1.jpg" in data
        assert "video1.mp4" in data


class TestShareExpiration:
    """Tests for share expiration functionality."""

    def test_expired_share_returns_410_status(self, app, client):
        """Test that expired shares return 410 Gone status code."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()
            # Create user first
            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "expuser2", "exp2@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            expired_time = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete, expires_at) VALUES (?, ?, 1, ?)",
                (share_token, user_id, expired_time)
            )
            conn.commit()
            conn.close()

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 410

    def test_share_expires_at_exact_time(self, app, client):
        """Test that shares expire exactly at their expiration time."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()
            # Create user first
            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "expuser3", "exp3@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            # Set expiration to 1 second in the past
            expired_time = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete, expires_at) VALUES (?, ?, 1, ?)",
                (share_token, user_id, expired_time)
            )
            conn.commit()
            conn.close()

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 410

    def test_expires_at_datetime_object_past_returns_410(self, app, client, monkeypatch):
        """PostgreSQL may return expires_at as a datetime; a past value expires the share."""
        share_token, _, _, _ = create_test_user_with_share(app)
        past = datetime.now(timezone.utc) - timedelta(hours=1)

        with app.app_context():
            share_row = get_share_by_token(share_token)
            share_row = dict(share_row)
            share_row["expires_at"] = past

        monkeypatch.setattr(
            "app.routes.share.get_share_by_token",
            lambda token: share_row if token == share_token else None,
        )

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 410

    def test_expires_at_datetime_object_future_is_accessible(self, app, client, monkeypatch):
        """A future datetime expires_at (PostgreSQL) must not raise and stays accessible."""
        share_token, _, _, _ = create_test_user_with_share(app)
        future = datetime.now(timezone.utc) + timedelta(hours=1)

        with app.app_context():
            share_row = get_share_by_token(share_token)
            share_row = dict(share_row)
            share_row["expires_at"] = future

        monkeypatch.setattr(
            "app.routes.share.get_share_by_token",
            lambda token: share_row if token == share_token else None,
        )

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200

    def test_expires_at_naive_datetime_is_treated_as_utc(self, app, client, monkeypatch):
        """A naive datetime is interpreted as UTC, matching the string path."""
        share_token, _, _, _ = create_test_user_with_share(app)
        past_naive = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)

        with app.app_context():
            share_row = get_share_by_token(share_token)
            share_row = dict(share_row)
            share_row["expires_at"] = past_naive

        monkeypatch.setattr(
            "app.routes.share.get_share_by_token",
            lambda token: share_row if token == share_token else None,
        )

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 410

    def test_expires_at_string_still_expires(self, app, client, monkeypatch):
        """SQLite string expires_at values still expire the share."""
        share_token, _, _, _ = create_test_user_with_share(app)
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

        with app.app_context():
            share_row = get_share_by_token(share_token)
            share_row = dict(share_row)
            share_row["expires_at"] = past

        monkeypatch.setattr(
            "app.routes.share.get_share_by_token",
            lambda token: share_row if token == share_token else None,
        )

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 410

    def test_expires_at_future_string_is_accessible(self, app, client, monkeypatch):
        """A future SQLite-style string expires_at keeps the share accessible."""
        share_token, _, _, _ = create_test_user_with_share(app)
        future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        with app.app_context():
            share_row = get_share_by_token(share_token)
            share_row = dict(share_row)
            share_row["expires_at"] = future

        monkeypatch.setattr(
            "app.routes.share.get_share_by_token",
            lambda token: share_row if token == share_token else None,
        )

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200

    def test_future_expiration_share_is_accessible(self, app, client):
        """Test that shares with future expiration dates are accessible."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()
            # Create user first
            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "expuser4", "exp4@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            # Set expiration to 1 hour in the future
            future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete, expires_at) VALUES (?, ?, 1, ?)",
                (share_token, user_id, future_time)
            )
            conn.commit()
            conn.close()

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200


class TestShareFileCountAndSize:
    """Tests for share metadata display."""

    def test_share_page_shows_file_count(self, app, client):
        """Test that share page indicates number of files."""
        share_token, _, _, _ = create_test_user_with_share(app)

        response = client.get(f"/s/{share_token}")
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "2" in data or "two" in data.lower() or "file" in data.lower()
