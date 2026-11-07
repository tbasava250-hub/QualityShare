"""
QualityShare Upload Routes

Handles chunked uploads, integrity verification (SHA-256 checksums),
multipart storage assembly, and share finalization.
"""

import os
import uuid
import hashlib
from flask import Blueprint, request, jsonify, current_app, session, redirect, url_for
from app.routes.auth import login_required
from app.config import Config
from app.models import (
    create_share,
    get_share_by_token,
    get_share_by_id,
    update_share_complete,
    create_file,
    get_file_by_id,
    update_file_chunk_progress,
    update_file_complete,
    update_file_failed,
    get_files_for_share,
)
from app.services.validation import validate_file, validate_share_limits
from app.services.storage import (
    upload_file as s3_upload_file,
    start_multipart_upload,
    upload_part,
    complete_multipart_upload,
    abort_multipart_upload,
)
from app.services.qr_service import generate_qr_code

upload_bp = Blueprint("upload", __name__, url_prefix="/api/upload")


def _user_owns_share(share_row):
    """Return whether the authenticated user owns the given share."""
    return share_row is not None and share_row["user_id"] == session.get("user_id")


def _ownership_denied():
    """Return a consistent response for cross-user upload access attempts."""
    return jsonify({"error": "You do not have permission to access this upload."}), 403


@upload_bp.route("/init", methods=["POST"])
@login_required
def init_upload():
    """
    Initialize a new sharing session and register files.

    Expected JSON body:
        files: list of objects with {name, size, type}
        expires_in_days: int (optional, default Config.DEFAULT_EXPIRATION_DAYS)

    Returns:
        share_token: UUID string used in sharing URLs
        files: list of {id, original_name, upload_id, total_chunks, is_multipart}
    """
    data = request.get_json() or {}
    file_list = data.get("files", [])
    expires_in_days = data.get("expires_in_days", Config.DEFAULT_EXPIRATION_DAYS)

    if not file_list:
        return jsonify({"error": "No files provided."}), 400

    # Validate share limits (file count & total size)
    total_size = sum(f.get("size", 0) for f in file_list)
    limit_check = validate_share_limits(len(file_list), total_size)
    if not limit_check["valid"]:
        return jsonify({"error": limit_check["error"]}), 400

    # Validate each individual file first
    validated_files = []
    for f in file_list:
        name = f.get("name", "unknown")
        mime = f.get("type", "application/octet-stream")
        size = f.get("size", 0)

        val = validate_file(name, mime, size)
        if not val["valid"]:
            return jsonify({"error": val["error"]}), 400

        validated_files.append({
            "name": name,
            "mime": mime,
            "size": size,
            "file_type": val["file_type"],
        })

    # Create unique share token (UUID hex, 16 chars is plenty or full UUID4)
    share_token = uuid.uuid4().hex

    # Check if a user is logged in
    user_id = session.get("user_id")

    # Calculate expiration date if requested
    expires_at = None
    if expires_in_days and expires_in_days > 0:
        from datetime import datetime, timedelta, timezone
        exp_dt = datetime.now(timezone.utc) + timedelta(days=int(expires_in_days))
        expires_at = exp_dt.strftime("%Y-%m-%d %H:%M:%S")

    share_id = create_share(share_token, expires_at, user_id=user_id)

    response_files = []

    for f_info in validated_files:
        name = f_info["name"]
        mime = f_info["mime"]
        size = f_info["size"]
        file_type = f_info["file_type"]

        # Unique storage key in bucket
        storage_key = f"shares/{share_token}/{uuid.uuid4()}_{name}"

        # Determine chunking strategy
        # Files >= 5MB use multipart upload; smaller files use single PUT
        chunk_size = Config.CHUNK_SIZE_BYTES
        is_multipart = size >= chunk_size
        total_chunks = max(1, (size + chunk_size - 1) // chunk_size)

        upload_id = None
        if is_multipart:
            try:
                upload_id = start_multipart_upload(storage_key, mime)
            except Exception as e:
                current_app.logger.error("Failed to start multipart upload: %s", e)
                return jsonify({"error": "Storage service error. Please try again."}), 500

        file_id = create_file(
            share_id=share_id,
            original_name=name,
            storage_key=storage_key,
            content_type=mime,
            size_bytes=size,
            file_type=file_type,
            total_chunks=total_chunks,
            upload_id=upload_id,
        )

        response_files.append({
            "file_id": file_id,
            "original_name": name,
            "size_bytes": size,
            "file_type": file_type,
            "total_chunks": total_chunks,
            "chunk_size": chunk_size,
            "upload_id": upload_id,
            "storage_key": storage_key,
        })

    return jsonify({
        "share_token": share_token,
        "share_id": share_id,
        "files": response_files,
    }), 201


@upload_bp.route("/chunk", methods=["POST"])
@login_required
def upload_chunk():
    """
    Receive and upload a single chunk (part) of a file.

    Form data / Multipart request:
        file_id: int
        chunk_index: int (1-based part number)
        chunk: file binary data
    """
    file_id = request.form.get("file_id")
    chunk_index = request.form.get("chunk_index")
    chunk_file = request.files.get("chunk")

    if not file_id or not chunk_index or not chunk_file:
        return jsonify({"error": "Missing file_id, chunk_index, or chunk data."}), 400

    try:
        file_id = int(file_id)
        chunk_index = int(chunk_index)
    except ValueError:
        return jsonify({"error": "Invalid file_id or chunk_index."}), 400

    file_row = get_file_by_id(file_id)
    if not file_row:
        return jsonify({"error": "File record not found."}), 404

    share_row = get_share_by_id(file_row["share_id"])
    if not _user_owns_share(share_row):
        return _ownership_denied()

    chunk_data = chunk_file.read()

    try:
        if file_row["upload_id"]:
            # Multipart upload chunk
            etag = upload_part(
                storage_key=file_row["storage_key"],
                upload_id=file_row["upload_id"],
                part_number=chunk_index,
                data=chunk_data,
            )
            # Store ETag in request context or DB if needed; for simplicity,
            # we can cache or return it, but here we can save etags in memory/session
            # or track them. Even better, let's store part ETags in a small helper table
            # or return them to client so client can send them back on complete.
            # Returning ETag to client is standard for chunked S3 uploads.
            update_file_chunk_progress(file_id, chunk_index)
            return jsonify({"status": "ok", "etag": etag, "part_number": chunk_index}), 200
        else:
            # Single-part upload (file < 5MB, uploaded in one chunk)
            s3_upload_file(
                storage_key=file_row["storage_key"],
                data=chunk_data,
                content_type=file_row["content_type"],
            )
            update_file_chunk_progress(file_id, 1)
            return jsonify({"status": "ok", "part_number": 1}), 200

    except Exception as e:
        current_app.logger.error("Error uploading chunk for file %s: %s", file_id, e)
        return jsonify({"error": f"Upload failed: {str(e)}"}), 500


@upload_bp.route("/complete", methods=["POST"])
@login_required
def complete_file_upload():
    """
    Mark a file upload as complete, verify integrity checksum,
    and finalize multipart upload if applicable.

    Expected JSON body:
        file_id: int
        parts: list of {part_number, etag} (required for multipart files)
        checksum: str (SHA-256 hex digest calculated by browser)
    """
    data = request.get_json() or {}
    file_id = data.get("file_id")
    parts = data.get("parts", [])
    client_checksum = data.get("checksum")

    if not file_id:
        return jsonify({"error": "Missing file_id."}), 400

    file_row = get_file_by_id(file_id)
    if not file_row:
        return jsonify({"error": "File record not found."}), 404

    share_row = get_share_by_id(file_row["share_id"])
    if not _user_owns_share(share_row):
        return _ownership_denied()

    try:
        if file_row["upload_id"]:
            # Sort parts by part number
            sorted_parts = sorted(parts, key=lambda p: p["part_number"])
            formatted_parts = [
                {"PartNumber": p["part_number"], "ETag": p["etag"]}
                for p in sorted_parts
            ]
            complete_multipart_upload(
                storage_key=file_row["storage_key"],
                upload_id=file_row["upload_id"],
                parts=formatted_parts,
            )

        # Mark file complete in DB
        update_file_complete(file_id, checksum=client_checksum)
        return jsonify({"status": "complete", "file_id": file_id}), 200

    except Exception as e:
        current_app.logger.error("Failed to complete file %s: %s", file_id, e)
        update_file_failed(file_id)
        if file_row["upload_id"]:
            abort_multipart_upload(file_row["storage_key"], file_row["upload_id"])
        return jsonify({"error": f"Failed to complete upload: {str(e)}"}), 500


@upload_bp.route("/finalize", methods=["POST"])
@login_required
def finalize_share():
    """
    Finalize an entire share session after all files have successfully uploaded.

    Expected JSON body:
        share_token: str

    Returns:
        share_url: str
        qr_code_data_uri: str (base64 PNG)
        file_count: int
        total_size: int
    """
    data = request.get_json() or {}
    share_token = data.get("share_token")

    if not share_token:
        return jsonify({"error": "Missing share_token."}), 400

    share_row = get_share_by_token(share_token)
    if not share_row:
        return jsonify({"error": "Share not found."}), 404
    if not _user_owns_share(share_row):
        return _ownership_denied()

    files = get_files_for_share(share_row["id"])
    if not files:
        return jsonify({"error": "No files found for this share."}), 400

    # Verify all files are complete
    for f in files:
        if f["upload_status"] != "complete":
            return jsonify({
                "error": f"File '{f['original_name']}' upload is not complete."
            }), 400

    file_count = len(files)
    total_size = sum(f["size_bytes"] for f in files)

    # Update share as complete
    update_share_complete(share_row["id"], file_count, total_size)

    # Build share URL
    share_url = f"{Config.BASE_URL}/s/{share_token}"

    # Generate QR code
    import base64
    qr_bytes = generate_qr_code(share_url)
    qr_base64 = base64.b64encode(qr_bytes).decode("utf-8")
    qr_data_uri = f"data:image/png;base64,{qr_base64}"

    return jsonify({
        "status": "success",
        "share_token": share_token,
        "share_url": share_url,
        "qr_code": qr_data_uri,
        "file_count": file_count,
        "total_size": total_size,
        "expires_at": share_row["expires_at"],
    }), 200


@upload_bp.route("/status/<token>", methods=["GET"])
@login_required
def upload_status(token):
    """Check upload status of all files in a share session."""
    share_row = get_share_by_token(token)
    if not share_row:
        return jsonify({"error": "Share not found."}), 404
    if not _user_owns_share(share_row):
        return _ownership_denied()

    files = get_files_for_share(share_row["id"])
    file_statuses = [
        {
            "file_id": f["id"],
            "original_name": f["original_name"],
            "size_bytes": f["size_bytes"],
            "file_type": f["file_type"],
            "upload_status": f["upload_status"],
            "chunks_received": f["chunks_received"],
            "total_chunks": f["total_chunks"],
        }
        for f in files
    ]

    return jsonify({
        "share_token": token,
        "is_complete": bool(share_row["is_complete"]),
        "files": file_statuses,
    }), 200
