"""
QualityShare Dashboard Routes

Provides personal dashboard for authenticated users to view uploads,
manage shares, generate QR codes, and delete their own files/shares.
"""

from flask import Blueprint, render_template, request, jsonify, session, abort
from app.routes.auth import login_required
from app.models import (
    get_db,
    get_share_by_token,
    get_files_for_share,
)
from app.services.storage import delete_files
from app.services.qr_service import generate_qr_code
from app.config import Config
import base64

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    """Render the personal dashboard for the logged-in user."""
    user_id = session.get("user_id")
    full_name = session.get("full_name", "User")
    username = session.get("username", "")

    conn = get_db()
    try:
        # Fetch all shares created by this user
        shares = conn.execute(
            """SELECT * FROM shares
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,)
        ).fetchall()

        # Compute stats & format shares with their files
        total_photos = 0
        total_videos = 0
        total_shares = len(shares)

        formatted_shares = []
        for s in shares:
            files = conn.execute(
                "SELECT * FROM files WHERE share_id = ? ORDER BY id",
                (s["id"],)
            ).fetchall()

            photos_in_share = sum(1 for f in files if f["file_type"] == "photo")
            videos_in_share = sum(1 for f in files if f["file_type"] == "video")

            total_photos += photos_in_share
            total_videos += videos_in_share

            # Format total size nicely
            size_bytes = s["total_size"]
            if size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            elif size_bytes < 1024 * 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
            else:
                size_str = f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

            share_url = f"{Config.BASE_URL}/s/{s['token']}"

            formatted_files = []
            for f in files:
                f_size = f["size_bytes"]
                if f_size < 1024 * 1024:
                    f_size_str = f"{f_size / 1024:.1f} KB"
                elif f_size < 1024 * 1024 * 1024:
                    f_size_str = f"{f_size / (1024 * 1024):.1f} MB"
                else:
                    f_size_str = f"{f_size / (1024 * 1024 * 1024):.2f} GB"

                formatted_files.append({
                    "id": f["id"],
                    "original_name": f["original_name"],
                    "file_type": f["file_type"],
                    "size_str": f_size_str,
                    "download_url": f"/api/download/{s['token']}/{f['id']}"
                })

            formatted_shares.append({
                "id": s["id"],
                "token": s["token"],
                "share_url": share_url,
                "created_at": s["created_at"],
                "expires_at": s["expires_at"],
                "file_count": s["file_count"],
                "total_size_str": size_str,
                "is_complete": bool(s["is_complete"]),
                "files": formatted_files,
            })

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        full_name=full_name,
        username=username,
        total_photos=total_photos,
        total_videos=total_videos,
        total_shares=total_shares,
        shares=formatted_shares,
    )


@dashboard_bp.route("/api/dashboard/share/<int:share_id>", methods=["DELETE"])
@login_required
def delete_share(share_id):
    """
    Delete a share and its associated files, enforcing strict user ownership.
    Removes files from object storage (RustFS) as well.
    """
    user_id = session.get("user_id")
    conn = get_db()
    try:
        # Verify ownership
        share = conn.execute(
            "SELECT * FROM shares WHERE id = ? AND user_id = ?",
            (share_id, user_id)
        ).fetchone()

        if not share:
            return jsonify({"error": "Share not found or unauthorized."}), 404

        # Get storage keys for all files in this share
        files = conn.execute(
            "SELECT storage_key FROM files WHERE share_id = ?",
            (share_id,)
        ).fetchall()
        storage_keys = [f["storage_key"] for f in files]

        # Delete from object storage
        if storage_keys:
            delete_files(storage_keys)

        # Delete share from DB (cascade deletes files)
        conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
        conn.commit()

        return jsonify({"status": "success", "message": "Share and files deleted successfully."}), 200

    except Exception as e:
        return jsonify({"error": f"Failed to delete share: {str(e)}"}), 500
    finally:
        conn.close()
