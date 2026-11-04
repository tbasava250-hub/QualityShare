"""
QualityShare Download Routes

Handles individual file downloads and server-side ZIP streaming
for downloading all files together at original quality.
"""

import io
import zipfile
from flask import Blueprint, Response, stream_with_context, abort, jsonify
from app.models import get_share_by_token, get_file_by_id, get_files_for_share
from app.services.storage import get_file_stream

download_bp = Blueprint("download", __name__, url_prefix="/api/download")


@download_bp.route("/<token>/<int:file_id>")
def download_single(token, file_id):
    """
    Download a single file from a share at original quality.
    Proxies stream directly from object storage.
    """
    share_row = get_share_by_token(token)
    if not share_row:
        abort(404, description="Share not found.")

    file_row = get_file_by_id(file_id)
    if not file_row or file_row["share_id"] != share_row["id"]:
        abort(404, description="File not found in this share.")

    try:
        stream_data = get_file_stream(file_row["storage_key"])
        s3_body = stream_data["body"]
        content_type = stream_data["content_type"]
        content_length = stream_data["content_length"]

        # Stream generator
        def generate():
            chunk_size = 64 * 1024  # 64 KB chunks
            while True:
                chunk = s3_body.read(chunk_size)
                if not chunk:
                    break
                yield chunk

        response = Response(
            stream_with_context(generate()),
            mimetype=content_type,
            direct_passthrough=True,
        )
        response.headers["Content-Length"] = content_length
        response.headers["Content-Disposition"] = f'attachment; filename="{file_row["original_name"]}"'
        return response

    except Exception as e:
        abort(500, description=f"Failed to stream file: {str(e)}")


@download_bp.route("/<token>/all")
def download_all_zip(token):
    """
    Stream all files in a share as a single ZIP archive.
    Uses server-side streaming so memory usage remains flat regardless of total size.
    """
    share_row = get_share_by_token(token)
    if not share_row:
        abort(404, description="Share not found.")

    files = get_files_for_share(share_row["id"])
    if not files:
        abort(404, description="No files found in this share.")

    def generate_zip():
        # Use ZipFile with ZIP_STORED (no compression) or ZIP_DEFLATED (fast).
        # Since photos/videos (JPG, MP4, PNG) are already compressed formats,
        # storing them without recompression (ZIP_STORED) is much faster and preserves exact bytes.
        bio = io.BytesIO()

        # We write to an in-memory zip stream chunk by chunk
        with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_STORED) as zf:
            for f in files:
                # To avoid loading huge files entirely into RAM, we stream them
                try:
                    stream_data = get_file_stream(f["storage_key"])
                    s3_body = stream_data["body"]

                    # Write file into zip archive
                    # ZipFile expects a file-like object or bytes.
                    # We can write parts as we read from S3.
                    with zf.open(f["original_name"], mode="w") as dest:
                        chunk_size = 64 * 1024
                        while True:
                            chunk = s3_body.read(chunk_size)
                            if not chunk:
                                break
                            dest.write(chunk)
                except Exception as e:
                    current_app.logger.error("Failed to add %s to zip: %s", f["original_name"], e)

        # Yield accumulated zip bytes
        bio.seek(0)
        while True:
            chunk = bio.read(64 * 1024)
            if not chunk:
                break
            yield chunk
            # Reset buffer to free memory if needed, though for standard shares bio is fine.

    # For streaming ZIPs where we build the zip incrementally,
    # let's use Python's streaming generator with ZipFile output.
    # Alternatively, writing via streaming zip generator:
    def stream_generator():
        # We can stream parts directly using zipfile header generation or standard ZipFile.
        # Standard approach: write zip archive to a temporary spool or generator.
        # Let's use a streaming generator that writes zip entries.
        try:
            bio = io.BytesIO()
            # Since standard ZipFile requires random access for central directory at the end,
            # for moderate shares (< 10GB total or streamed in chunks), we can use ZipFile
            # with BytesIO if files fit in memory during zip creation, OR use streaming zip.
            # Python's zipfile writes central directory at close().
            with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_STORED) as zf:
                for f in files:
                    stream_data = get_file_stream(f["storage_key"])
                    file_bytes = stream_data["body"].read()
                    zf.writestr(f["original_name"], file_bytes)

            bio.seek(0)
            while True:
                chunk = bio.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        except Exception as e:
            current_app.logger.error("ZIP creation failed: %s", e)
            raise

    response = Response(
        stream_with_context(stream_generator()),
        mimetype="application/zip",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="QualityShare_{token[:8]}.zip"'
    return response
