"""
QualityShare Storage Service

Handles all communication with S3-compatible object storage (AWS S3, MinIO, etc.).
Supports:
  - Single-part upload (files < 5 MB)
  - Multipart upload (files >= 5 MB) for resumable large uploads
  - Downloading file streams
  - Deleting objects

The rest of the app never imports boto3 directly — it goes through this module.
"""

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from app.config import Config


def _get_client():
    """
    Create and return an S3 client configured from environment variables.

    Uses path-style access so it works with MinIO (local dev) and AWS S3.
    """
    return boto3.client(
        "s3",
        endpoint_url=Config.STORAGE_ENDPOINT,
        aws_access_key_id=Config.STORAGE_ACCESS_KEY,
        aws_secret_access_key=Config.STORAGE_SECRET_KEY,
        region_name=Config.STORAGE_REGION,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def ensure_bucket_exists():
    """
    Create the storage bucket if it doesn't already exist.
    Called once at app startup.
    """
    client = _get_client()
    try:
        client.head_bucket(Bucket=Config.STORAGE_BUCKET)
    except ClientError:
        client.create_bucket(Bucket=Config.STORAGE_BUCKET)


# ---------------------------------------------------------------------------
# Single-part upload (small files, under the chunk size)
# ---------------------------------------------------------------------------

def upload_file(storage_key, data, content_type):
    """
    Upload a complete file in one request.

    Args:
        storage_key: the object key in the bucket (e.g. "shares/abc123/photo.jpg")
        data: file bytes (a bytes object or file-like object)
        content_type: MIME type (e.g. "image/jpeg")
    """
    client = _get_client()
    client.put_object(
        Bucket=Config.STORAGE_BUCKET,
        Key=storage_key,
        Body=data,
        ContentType=content_type,
    )


# ---------------------------------------------------------------------------
# Multipart upload (large files, resumable)
# ---------------------------------------------------------------------------

def start_multipart_upload(storage_key, content_type):
    """
    Begin a multipart upload.

    Returns the upload_id which must be passed to upload_part and
    complete_multipart_upload.
    """
    client = _get_client()
    response = client.create_multipart_upload(
        Bucket=Config.STORAGE_BUCKET,
        Key=storage_key,
        ContentType=content_type,
    )
    return response["UploadId"]


def upload_part(storage_key, upload_id, part_number, data):
    """
    Upload one chunk (part) of a multipart upload.

    Args:
        storage_key: same key used in start_multipart_upload
        upload_id: the id returned by start_multipart_upload
        part_number: 1-based index of this chunk
        data: chunk bytes

    Returns the ETag of the uploaded part (needed to complete the upload).
    """
    client = _get_client()
    response = client.upload_part(
        Bucket=Config.STORAGE_BUCKET,
        Key=storage_key,
        UploadId=upload_id,
        PartNumber=part_number,
        Body=data,
    )
    return response["ETag"]


def complete_multipart_upload(storage_key, upload_id, parts):
    """
    Finish a multipart upload by assembling all parts.

    Args:
        storage_key: same key used throughout
        upload_id: the id from start_multipart_upload
        parts: list of {"PartNumber": int, "ETag": str} dicts
    """
    client = _get_client()
    client.complete_multipart_upload(
        Bucket=Config.STORAGE_BUCKET,
        Key=storage_key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )


def abort_multipart_upload(storage_key, upload_id):
    """Cancel a multipart upload and discard uploaded parts."""
    client = _get_client()
    try:
        client.abort_multipart_upload(
            Bucket=Config.STORAGE_BUCKET,
            Key=storage_key,
            UploadId=upload_id,
        )
    except ClientError:
        pass  # Already aborted or doesn't exist


# ---------------------------------------------------------------------------
# Download / stream
# ---------------------------------------------------------------------------

def get_file_stream(storage_key):
    """
    Get a streaming response for a stored file.

    Returns a dict with:
        body: a StreamingBody (file-like, call .read() or iterate)
        content_type: the MIME type
        content_length: size in bytes
    """
    client = _get_client()
    response = client.get_object(
        Bucket=Config.STORAGE_BUCKET,
        Key=storage_key,
    )
    return {
        "body": response["Body"],
        "content_type": response["ContentType"],
        "content_length": response["ContentLength"],
    }


def get_file_size(storage_key):
    """Return the size (in bytes) of an object in storage."""
    client = _get_client()
    response = client.head_object(
        Bucket=Config.STORAGE_BUCKET,
        Key=storage_key,
    )
    return response["ContentLength"]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_file(storage_key):
    """Delete a single object from storage."""
    client = _get_client()
    client.delete_object(
        Bucket=Config.STORAGE_BUCKET,
        Key=storage_key,
    )


def delete_files(storage_keys):
    """Delete multiple objects from storage in one request (up to 1000)."""
    if not storage_keys:
        return
    client = _get_client()
    # S3 delete_objects accepts up to 1000 keys at a time
    for i in range(0, len(storage_keys), 1000):
        batch = storage_keys[i : i + 1000]
        client.delete_objects(
            Bucket=Config.STORAGE_BUCKET,
            Delete={
                "Objects": [{"Key": key} for key in batch],
                "Quiet": True,
            },
        )
