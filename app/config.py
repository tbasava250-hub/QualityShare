"""
QualityShare Configuration

Loads settings from environment variables (.env file).
All storage credentials and app secrets live here — never in frontend code.
"""

import os
from dotenv import load_dotenv

# Load .env file into os.environ
load_dotenv()


class Config:
    """Base configuration loaded from environment variables."""

    # Flask core
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-fallback-key")
    DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    # Database — SQLite file stored alongside the app
    BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    DATABASE_PATH = os.path.join(BASE_DIR, "qualityshare.db")

    # S3-compatible storage
    STORAGE_ENDPOINT = os.environ.get("STORAGE_ENDPOINT", "http://localhost:9000")
    STORAGE_ACCESS_KEY = os.environ.get("STORAGE_ACCESS_KEY", "minioadmin")
    STORAGE_SECRET_KEY = os.environ.get("STORAGE_SECRET_KEY", "minioadmin")
    STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "qualityshare")
    STORAGE_REGION = os.environ.get("STORAGE_REGION", "us-east-1")

    # Application URL (used to build share links and QR codes)
    BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")

    # Upload limits
    MAX_FILES_PER_SHARE = int(os.environ.get("MAX_FILES_PER_SHARE", 100))
    MAX_FILE_SIZE_BYTES = int(os.environ.get("MAX_FILE_SIZE_BYTES", 2_147_483_648))        # 2 GB
    MAX_TOTAL_SHARE_SIZE_BYTES = int(os.environ.get("MAX_TOTAL_SHARE_SIZE_BYTES", 10_737_418_240))  # 10 GB
    CHUNK_SIZE_BYTES = int(os.environ.get("CHUNK_SIZE_BYTES", 5_242_880))                   # 5 MB
    DEFAULT_EXPIRATION_DAYS = int(os.environ.get("DEFAULT_EXPIRATION_DAYS", 7))

    # Allowed file types (MIME type → list of extensions)
    ALLOWED_PHOTO_TYPES = {
        "image/jpeg": [".jpg", ".jpeg"],
        "image/png": [".png"],
        "image/webp": [".webp"],
        "image/heic": [".heic"],
    }

    ALLOWED_VIDEO_TYPES = {
        "video/mp4": [".mp4"],
        "video/quicktime": [".mov"],
        "video/x-msvideo": [".avi"],
        "video/x-matroska": [".mkv"],
    }

    @property
    def allowed_extensions(self):
        """Return a flat set of all allowed file extensions."""
        extensions = set()
        for ext_list in self.ALLOWED_PHOTO_TYPES.values():
            extensions.update(ext_list)
        for ext_list in self.ALLOWED_VIDEO_TYPES.values():
            extensions.update(ext_list)
        return extensions

    @property
    def allowed_mime_types(self):
        """Return a flat set of all allowed MIME types."""
        return set(self.ALLOWED_PHOTO_TYPES.keys()) | set(self.ALLOWED_VIDEO_TYPES.keys())
