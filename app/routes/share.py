"""
QualityShare Share Routes

Renders the receiver download page for a given sharing token,
displaying the combined photo and video gallery.
"""

from flask import Blueprint, render_template, abort, current_app
from app.models import get_share_by_token, get_files_for_share

share_bp = Blueprint("share", __name__)


@share_bp.route("/s/<token>")
def view_share(token):
    """
    Render the receiver's download page for a valid share token.
    Displays all photos and videos in a combined gallery with download options.
    """
    share_row = get_share_by_token(token)
    if not share_row:
        abort(404, description="This share link does not exist.")

    # Check if share is complete
    if not share_row["is_complete"]:
        abort(404, description="This share is still being uploaded.")

    # Check expiration if set
    if share_row["expires_at"]:
        from datetime import datetime, timezone
        raw_expires_at = share_row["expires_at"]
        try:
            if isinstance(raw_expires_at, datetime):
                # PostgreSQL returns a datetime; treat naive values as UTC.
                expires_at = raw_expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                else:
                    expires_at = expires_at.astimezone(timezone.utc)
            else:
                # SQLite datetime format: 'YYYY-MM-DD HH:MM:SS'
                expires_at = datetime.strptime(raw_expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                abort(410, description="This share link has expired.")
        except (TypeError, ValueError):
            pass

    files = get_files_for_share(share_row["id"])

    # Format file list for template
    file_list = []
    for f in files:
        # Format file size nicely
        size_bytes = f["size_bytes"]
        if size_bytes < 1024 * 1024:
            size_str = f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            size_str = f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

        file_list.append({
            "id": f["id"],
            "original_name": f["original_name"],
            "file_type": f["file_type"],
            "content_type": f["content_type"],
            "size_str": size_str,
            "download_url": f"/api/download/{token}/{f['id']}",
        })

    return render_template(
        "share.html",
        token=token,
        file_count=len(file_list),
        files=file_list,
        created_at=share_row["created_at"],
        expires_at=share_row["expires_at"],
    )
