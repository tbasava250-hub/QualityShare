"""
Tests for upload routes and file handling.

Run with:  python -m pytest tests/test_upload.py -v
"""

import sys
import os
import io
import json
import pytest
import tempfile
import shutil

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.models import get_db, init_db


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


class TestUploadAPI:
    """Tests for the /api/upload endpoints."""

    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.get_json() == {"status": "ok"}

    def test_init_upload_validation_error(self, client):
        # Register and log in first
        client.post("/register", json={
            "full_name": "Test User",
            "username": "uploadtest",
            "email": "uploadtest@example.com",
            "password": "password123",
            "confirm_password": "password123"
        }, headers={"Content-Type": "application/json"})

        # Empty file list
        response = client.post("/api/upload/init", json={"files": []})
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_init_upload_invalid_extension(self, client):
        client.post("/register", json={
            "full_name": "Test User",
            "username": "uploadtest2",
            "email": "uploadtest2@example.com",
            "password": "password123",
            "confirm_password": "password123"
        }, headers={"Content-Type": "application/json"})

        response = client.post("/api/upload/init", json={
            "files": [{"name": "script.exe", "size": 1000, "type": "application/x-msdownload"}]
        })
        assert response.status_code == 400
        assert "not supported" in response.get_json()["error"]

    def test_unauthenticated_upload_access_denied(self, client):
        # Ensure logged out
        client.get("/logout")

        # Try POST /api/upload/init without login
        res = client.post("/api/upload/init", json={
            "files": [{"name": "photo.jpg", "size": 1000, "type": "image/jpeg"}]
        })
        assert res.status_code == 401
        assert res.get_json()["error"] == "Authentication required."

        # Try GET /api/upload/status/sometoken without login
        res_status = client.get("/api/upload/status/sometoken")
        assert res_status.status_code == 401
        assert res_status.get_json()["error"] == "Authentication required."

    def test_init_upload_success_mock(self, client, monkeypatch):
        client.post("/register", json={
            "full_name": "Test User",
            "username": "uploadtest3",
            "email": "uploadtest3@example.com",
            "password": "password123",
            "confirm_password": "password123"
        }, headers={"Content-Type": "application/json"})
        # Mock storage start_multipart_upload so we don't need running MinIO for unit tests
        from app.routes import upload as upload_module
        monkeypatch.setattr(upload_module, "start_multipart_upload", lambda key, mime: "mock-upload-id")

        response = client.post("/api/upload/init", json={
            "files": [
                {"name": "photo.jpg", "size": 10_000_000, "type": "image/jpeg"}, # 10MB -> multipart
                {"name": "clip.mp4", "size": 2_000_000, "type": "video/mp4"},   # 2MB -> single
            ]
        })
        assert response.status_code == 201
        data = response.get_json()
        assert "share_token" in data
        assert len(data["files"]) == 2
        assert data["files"][0]["original_name"] == "photo.jpg"
        assert data["files"][0]["upload_id"] == "mock-upload-id"
        assert data["files"][1]["upload_id"] is None

    def test_upload_ownership_isolation_and_public_access(self, app, client, monkeypatch):
        """Only a share owner may mutate/read its upload session; shares stay public."""
        from app.routes import download as download_module
        from app.routes import upload as upload_module

        monkeypatch.setattr(upload_module, "s3_upload_file", lambda storage_key, data, content_type: None)
        monkeypatch.setattr(download_module, "get_file_stream", lambda storage_key: {
            "body": io.BytesIO(b"photo bytes"),
            "content_type": "image/jpeg",
            "content_length": len(b"photo bytes"),
        })

        # Create independent browser sessions for User A and User B.
        user_a = client
        user_b = app.test_client()
        for user_client, suffix in ((user_a, "a"), (user_b, "b")):
            response = user_client.post("/register", json={
                "full_name": f"User {suffix.upper()}",
                "username": f"owner_{suffix}",
                "email": f"owner_{suffix}@example.com",
                "password": "password123",
                "confirm_password": "password123",
            })
            assert response.status_code == 201

        # Each authenticated user creates an independent upload session.
        share_a = user_a.post("/api/upload/init", json={
            "files": [{"name": "a.jpg", "size": 100, "type": "image/jpeg"}]
        }).get_json()
        share_b = user_b.post("/api/upload/init", json={
            "files": [{"name": "b.jpg", "size": 100, "type": "image/jpeg"}]
        }).get_json()
        token_b = share_b["share_token"]
        file_id_b = share_b["files"][0]["file_id"]

        # Both users can inspect their own sessions.
        assert user_a.get(f"/api/upload/status/{share_a['share_token']}").status_code == 200
        assert user_b.get(f"/api/upload/status/{token_b}").status_code == 200

        # User A cannot access or mutate User B's file/share by changing IDs/tokens.
        denied_chunk = user_a.post(
            "/api/upload/chunk",
            data={
                "file_id": str(file_id_b),
                "chunk_index": "1",
                "chunk": (io.BytesIO(b"attacker bytes"), "chunk.bin"),
            },
            content_type="multipart/form-data",
        )
        assert denied_chunk.status_code == 403

        denied_complete = user_a.post("/api/upload/complete", json={
            "file_id": file_id_b,
            "checksum": "attacker-checksum",
        })
        assert denied_complete.status_code == 403

        denied_finalize = user_a.post("/api/upload/finalize", json={"share_token": token_b})
        assert denied_finalize.status_code == 403

        denied_status = user_a.get(f"/api/upload/status/{token_b}")
        assert denied_status.status_code == 403

        # User B can upload, complete, finalize, and inspect their own session.
        own_chunk = user_b.post(
            "/api/upload/chunk",
            data={
                "file_id": str(file_id_b),
                "chunk_index": "1",
                "chunk": (io.BytesIO(b"photo bytes"), "b.jpg"),
            },
            content_type="multipart/form-data",
        )
        assert own_chunk.status_code == 200
        assert user_b.post("/api/upload/complete", json={
            "file_id": file_id_b,
            "checksum": "owner-checksum",
        }).status_code == 200
        assert user_b.get(f"/api/upload/status/{token_b}").status_code == 200
        assert user_b.post("/api/upload/finalize", json={"share_token": token_b}).status_code == 200

        # Public share viewing and file download do not require authentication.
        anonymous = app.test_client()
        share_page = anonymous.get(f"/s/{token_b}")
        assert share_page.status_code == 200
        assert b"b.jpg" in share_page.data

        download = anonymous.get(f"/api/download/{token_b}/{file_id_b}")
        assert download.status_code == 200
        assert download.data == b"photo bytes"
