"""
Tests for the validation service.

Run with:  python -m pytest tests/test_validation.py -v
"""

import sys
import os

# Add project root to path so we can import the app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.validation import validate_file, validate_share_limits
from app.config import Config


class TestValidateFile:
    """Tests for individual file validation."""

    def test_valid_jpg(self):
        result = validate_file("photo.jpg", "image/jpeg", 1_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "photo"
        assert result["error"] is None

    def test_valid_png(self):
        result = validate_file("screenshot.png", "image/png", 5_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "photo"

    def test_valid_mp4(self):
        result = validate_file("video.mp4", "video/mp4", 500_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "video"

    def test_valid_mov(self):
        result = validate_file("clip.MOV", "video/quicktime", 1_000_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "video"

    def test_valid_webp(self):
        result = validate_file("image.webp", "image/webp", 2_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "photo"

    def test_valid_heic(self):
        result = validate_file("photo.heic", "image/heic", 3_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "photo"

    def test_valid_avi(self):
        result = validate_file("movie.avi", "video/x-msvideo", 800_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "video"

    def test_valid_mkv(self):
        result = validate_file("film.mkv", "video/x-matroska", 1_500_000_000)
        assert result["valid"] is True
        assert result["file_type"] == "video"

    def test_rejected_exe(self):
        result = validate_file("malware.exe", "application/x-msdownload", 1_000)
        assert result["valid"] is False
        assert "not supported" in result["error"]

    def test_rejected_no_extension(self):
        result = validate_file("noext", "image/jpeg", 1_000)
        assert result["valid"] is False
        assert "no extension" in result["error"]

    def test_rejected_pdf(self):
        result = validate_file("doc.pdf", "application/pdf", 1_000)
        assert result["valid"] is False

    def test_rejected_too_large(self):
        # 3 GB exceeds the 2 GB limit
        result = validate_file("huge.mp4", "video/mp4", 3_000_000_000)
        assert result["valid"] is False
        assert "too large" in result["error"]

    def test_case_insensitive_extension(self):
        result = validate_file("PHOTO.JPG", "image/jpeg", 1_000_000)
        assert result["valid"] is True

    def test_octet_stream_mime_with_valid_extension(self):
        """Browsers sometimes send application/octet-stream for valid files."""
        result = validate_file("video.mp4", "application/octet-stream", 1_000_000)
        assert result["valid"] is True


class TestValidateShareLimits:
    """Tests for share-level limit validation."""

    def test_valid_share(self):
        result = validate_share_limits(10, 500_000_000)
        assert result["valid"] is True

    def test_too_many_files(self):
        result = validate_share_limits(101, 1_000)
        assert result["valid"] is False
        assert "Too many files" in result["error"]

    def test_no_files(self):
        result = validate_share_limits(0, 0)
        assert result["valid"] is False
        assert "At least one file" in result["error"]

    def test_total_size_exceeded(self):
        # 11 GB exceeds the 10 GB limit
        result = validate_share_limits(5, 11_000_000_000)
        assert result["valid"] is False
        assert "limit" in result["error"]

    def test_exactly_at_limits(self):
        result = validate_share_limits(
            Config.MAX_FILES_PER_SHARE,
            Config.MAX_TOTAL_SHARE_SIZE_BYTES,
        )
        assert result["valid"] is True
