"""
QualityShare Validation Service

Validates uploaded files before they are stored:
  - Checks file extension against the allowed list
  - Checks MIME type against the allowed list
  - Enforces individual file size limits
  - Enforces total share size limits
  - Enforces max file count per share
"""

import os
from app.config import Config

# Build lookup structures once at import time
_config = Config()
_ALLOWED_EXTENSIONS = _config.allowed_extensions
_ALLOWED_MIME_TYPES = _config.allowed_mime_types

# Map extensions to their file category
_PHOTO_EXTENSIONS = set()
for exts in Config.ALLOWED_PHOTO_TYPES.values():
    _PHOTO_EXTENSIONS.update(exts)

_VIDEO_EXTENSIONS = set()
for exts in Config.ALLOWED_VIDEO_TYPES.values():
    _VIDEO_EXTENSIONS.update(exts)


def validate_file(filename, content_type, size_bytes):
    """
    Validate a single file's name, type, and size.

    Args:
        filename: the original file name (e.g. "vacation.mp4")
        content_type: the MIME type sent by the browser
        size_bytes: the declared file size in bytes

    Returns:
        A dict with:
            valid (bool): whether the file is acceptable
            error (str or None): human-readable reason if invalid
            file_type (str or None): "photo" or "video" if valid
    """
    # 1. Check that the filename has an extension
    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    if not ext:
        return {
            "valid": False,
            "error": f"File '{filename}' has no extension.",
            "file_type": None,
        }

    # 2. Check extension is allowed
    if ext not in _ALLOWED_EXTENSIONS:
        return {
            "valid": False,
            "error": (
                f"File type '{ext}' is not supported. "
                f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
            ),
            "file_type": None,
        }

    # 3. Check MIME type is allowed
    if content_type not in _ALLOWED_MIME_TYPES:
        # Some browsers send generic types — allow if extension matches
        # but still block clearly wrong types
        if not content_type.startswith(("image/", "video/", "application/octet-stream")):
            return {
                "valid": False,
                "error": f"MIME type '{content_type}' is not allowed.",
                "file_type": None,
            }

    # 4. Check individual file size
    if size_bytes > Config.MAX_FILE_SIZE_BYTES:
        max_mb = Config.MAX_FILE_SIZE_BYTES / (1024 * 1024)
        return {
            "valid": False,
            "error": f"File '{filename}' is too large. Maximum size is {max_mb:.0f} MB.",
            "file_type": None,
        }

    # 5. Determine file type
    file_type = "photo" if ext in _PHOTO_EXTENSIONS else "video"

    return {
        "valid": True,
        "error": None,
        "file_type": file_type,
    }


def validate_share_limits(file_count, total_size):
    """
    Validate that a share doesn't exceed global limits.

    Args:
        file_count: how many files the user wants to upload
        total_size: combined size of all files in bytes

    Returns:
        A dict with:
            valid (bool): whether limits are satisfied
            error (str or None): human-readable reason if invalid
    """
    if file_count > Config.MAX_FILES_PER_SHARE:
        return {
            "valid": False,
            "error": f"Too many files. Maximum is {Config.MAX_FILES_PER_SHARE} files per share.",
        }

    if file_count < 1:
        return {
            "valid": False,
            "error": "At least one file is required.",
        }

    if total_size > Config.MAX_TOTAL_SHARE_SIZE_BYTES:
        max_gb = Config.MAX_TOTAL_SHARE_SIZE_BYTES / (1024 ** 3)
        return {
            "valid": False,
            "error": f"Total size exceeds the {max_gb:.0f} GB limit.",
        }

    return {"valid": True, "error": None}
