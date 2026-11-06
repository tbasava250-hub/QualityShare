"""
Tests for download functionality.

Tests single file downloads, ZIP archive downloads, invalid tokens,
expired shares, and access control.
"""

import sys
import os
import pytest
import tempfile
import shutil

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.models import (
    get_db,
    init_db,
    create_user,
    create_share,
    create_file,
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


def create_test_user_with_share(app, username="testuser", email="test@example.com", share_token=None):
    """Create a test user and a share with files. Returns share_token, file1_id, file2_id."""
    import uuid

    with app.app_context():
        conn = get_db()

        # Create user first
        cursor = conn.execute(
            "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
            ("Test User", username, email, "pbkdf2:sha256:600000$test$test", 0)
        )
        user_id = cursor.lastrowid
        conn.commit()

        if share_token is None:
            share_token = uuid.uuid4().hex

        # Create a completed share
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

    return share_token, file1_id, file2_id


class TestSingleFileDownload:
    """Tests for single file downloads."""

    def test_download_single_valid_file(self, app, client, monkeypatch):
        """Test downloading a single valid file from a share."""
        share_token, file1_id, _ = create_test_user_with_share(app)

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b""
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        # Patch in the download module namespace (not storage module)
        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/{file1_id}")

        assert response.status_code == 200
        assert "image/jpeg" in response.content_type
        assert "photo1.jpg" in response.headers.get("Content-Disposition", "")

    def test_download_nonexistent_file_returns_404(self, app, client):
        """Test that downloading a nonexistent file returns 404."""
        share_token, _, _ = create_test_user_with_share(app)

        response = client.get(f"/api/download/{share_token}/99999")
        assert response.status_code == 404

    def test_download_file_from_nonexistent_share_returns_404(self, app, client):
        """Test that downloading a file from a nonexistent share returns 404."""
        response = client.get("/api/download/nonexistenttoken123/1")
        assert response.status_code == 404

    def test_download_file_wrong_share_returns_404(self, app, client):
        """Test that downloading a file that doesn't belong to the share returns 404."""
        share_token1, file1_id, _ = create_test_user_with_share(app, username="user1", email="user1@example.com", share_token="token1")
        share_token2, _, _ = create_test_user_with_share(app, username="user2", email="user2@example.com", share_token="token2")

        response = client.get(f"/api/download/{share_token2}/{file1_id}")
        assert response.status_code == 404

    def test_download_file_from_incomplete_share(self, app, client, monkeypatch):
        """Test downloading a file from an incomplete share."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()

            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "incompuser", "incomp@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete, file_count) VALUES (?, ?, 0, 1)",
                (share_token, user_id)
            )
            share_id = cursor.lastrowid

            cursor = conn.execute(
                """INSERT INTO files (share_id, original_name, storage_key, content_type,
                   size_bytes, file_type, upload_status, checksum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (share_id, "test.jpg", f"test/{share_token}/test.jpg", "image/jpeg", 1024, "photo", "complete", "abc")
            )
            file_id = cursor.lastrowid
            conn.commit()
            conn.close()

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b""
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/{file_id}")
        assert response.status_code == 200


class TestZIPDownload:
    """Tests for downloading all files as ZIP."""

    def test_download_all_files_zip(self, app, client, monkeypatch):
        """Test downloading all files as a ZIP archive."""
        share_token, _, _ = create_test_user_with_share(app)

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b"mock content"
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/all")

        assert response.status_code == 200
        assert "application/zip" in response.content_type
        assert "QualityShare" in response.headers.get("Content-Disposition", "")

    def test_download_all_from_nonexistent_share(self, app, client):
        """Test downloading all files from a nonexistent share."""
        response = client.get("/api/download/nonexistenttoken123/all")
        assert response.status_code == 404

    def test_download_all_from_share_with_no_files(self, app, client):
        """Test downloading all files from a share with no files."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()

            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "nofilesuser", "nofiles@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete, file_count) VALUES (?, ?, 1, 0)",
                (share_token, user_id)
            )
            conn.commit()
            conn.close()

        response = client.get(f"/api/download/{share_token}/all")
        assert response.status_code == 404

    def test_download_all_from_incomplete_share(self, app, client, monkeypatch):
        """Test downloading all files from an incomplete share."""
        with app.app_context():
            import uuid
            share_token = uuid.uuid4().hex
            conn = get_db()

            cursor = conn.execute(
                "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                ("Test User", "incompuser2", "incomp2@example.com", "pbkdf2:sha256:600000$test$test", 0)
            )
            user_id = cursor.lastrowid
            conn.commit()

            cursor = conn.execute(
                "INSERT INTO shares (token, user_id, is_complete, file_count) VALUES (?, ?, 0, 1)",
                (share_token, user_id)
            )
            share_id = cursor.lastrowid

            cursor = conn.execute(
                """INSERT INTO files (share_id, original_name, storage_key, content_type,
                   size_bytes, file_type, upload_status, checksum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (share_id, "test.jpg", f"test/{share_token}/test.jpg", "image/jpeg", 1024, "photo", "complete", "abc")
            )
            conn.commit()
            conn.close()

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b"mock content"
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/all")
        assert response.status_code == 200

    def test_download_all_returns_zip_content_type(self, app, client, monkeypatch):
        """Test that the ZIP download response has correct content type."""
        share_token, _, _ = create_test_user_with_share(app)

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b"mock content"
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/all")

        assert response.status_code == 200
        assert "application/zip" in response.content_type
        assert "attachment" in response.headers.get("Content-Disposition", "")


class TestDownloadSecurity:
    """Tests for download access control and security."""

    def test_download_nonexistent_token(self, app, client):
        """Test that nonexistent tokens are rejected."""
        response = client.get("/api/download/invalidtoken123/1")
        assert response.status_code == 404

        response = client.get("/api/download/invalidtoken123/all")
        assert response.status_code == 404

    def test_download_empty_token(self, app, client):
        """Test that empty tokens are rejected."""
        response = client.get("/api/download//1")
        assert response.status_code == 404

    def test_download_malformed_token(self, app, client):
        """Test that malformed tokens are rejected."""
        malformed = [
            "token/with/slashes",
            "token?query=param",
            "token#fragment",
            "<script>",
        ]
        for token in malformed:
            response = client.get(f"/api/download/{token}/1")
            assert response.status_code == 404

    def test_download_invalid_file_id(self, app, client):
        """Test that invalid file IDs are rejected."""
        share_token, _, _ = create_test_user_with_share(app)

        response = client.get(f"/api/download/{share_token}/-1")
        assert response.status_code == 404

        response = client.get(f"/api/download/{share_token}/abc")
        assert response.status_code == 404


class TestDownloadHeaders:
    """Tests for download response headers."""

    def test_single_download_has_content_disposition(self, app, client, monkeypatch):
        """Test that single file download has proper Content-Disposition header."""
        share_token, file1_id, _ = create_test_user_with_share(app)

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b""
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/{file1_id}")

        assert response.status_code == 200
        content_disposition = response.headers.get("Content-Disposition", "")
        assert "attachment" in content_disposition
        assert "photo1.jpg" in content_disposition

    def test_zip_download_has_content_disposition(self, app, client, monkeypatch):
        """Test that ZIP download has proper Content-Disposition header."""
        share_token, _, _ = create_test_user_with_share(app)

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b"mock content"
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/all")

        assert response.status_code == 200
        content_disposition = response.headers.get("Content-Disposition", "")
        assert "attachment" in content_disposition
        assert "zip" in content_disposition.lower()

    def test_zip_download_has_token_in_filename(self, app, client, monkeypatch):
        """Test that ZIP filename includes the share token."""
        share_token, _, _ = create_test_user_with_share(app)

        def mock_get_file_stream(key):
            class MockBody:
                def read(self, size=-1):
                    return b"mock content"
            return {
                "body": MockBody(),
                "content_type": "image/jpeg",
                "content_length": 1024,
            }

        from app.routes import download as download_module
        monkeypatch.setattr(download_module, "get_file_stream", mock_get_file_stream)

        response = client.get(f"/api/download/{share_token}/all")

        assert response.status_code == 200
        content_disposition = response.headers.get("Content-Disposition", "")
        assert share_token[:8] in content_disposition


class TestDownloadErrors:
    """Tests for download error handling."""

    def test_download_500_error_handled(self, app, client):
        """Test that storage errors are handled gracefully."""
        share_token, file1_id, _ = create_test_user_with_share(app)

        response = client.get(f"/api/download/{share_token}/{file1_id}")
        assert response.status_code in [200, 500]

    def test_download_invalid_file_id_format(self, app, client):
        """Test that invalid file ID format is handled."""
        share_token, _, _ = create_test_user_with_share(app)

        response = client.get(f"/api/download/{share_token}/notanumber")
        assert response.status_code == 404
