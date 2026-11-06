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
        # Empty file list
        response = client.post("/api/upload/init", json={"files": []})
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_init_upload_invalid_extension(self, client):
        response = client.post("/api/upload/init", json={
            "files": [{"name": "script.exe", "size": 1000, "type": "application/x-msdownload"}]
        })
        assert response.status_code == 400
        assert "not supported" in response.get_json()["error"]

    def test_init_upload_success_mock(self, client, monkeypatch):
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
