"""
QualityShare Admin Dashboard Routes

Provides an admin-only dashboard for:
- Viewing system-wide statistics
- Managing registered users
- Monitoring file storage
- Auditing uploads

SECURITY:
- Only authenticated administrators can access
- Never exposes credentials or password hashes
- Never exposes private object URLs publicly
- Requires confirmation for destructive operations
"""

from flask import Blueprint, render_template, request, jsonify, session, abort, current_app, redirect, url_for
from flask_paginate import Pagination, get_page_args
from app.routes.auth import login_required
from app.models import (
    get_db,
    get_user_by_id,
    get_user_by_username,
    get_user_by_email,
)
from app.services.storage import _get_client, Config
from app.config import Config as AppConfig

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def inject_config():
    """Inject config into template context."""
    return {"config": AppConfig}


admin_bp.app_context_processor(inject_config)


def require_admin():
    """Check if current user is an administrator."""
    if "user_id" not in session:
        return False
    user_id = session["user_id"]
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT is_admin FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return user and user["is_admin"] == 1
    finally:
        conn.close()


def admin_required(f):
    """Decorator to protect admin routes."""
    @login_required
    def decorated_function(*args, **kwargs):
        if not require_admin():
            abort(403)
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function


@admin_bp.route("")
@admin_required
def index_redirect():
    """Redirect /admin to /admin/ for consistent routing."""
    return redirect(url_for("admin.index"))


@admin_bp.route("/")
@admin_required
def index():
    """Admin dashboard - summary statistics."""
    conn = get_db()
    try:
        # Total registered users
        total_users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()["count"]

        # Total uploaded files
        total_files = conn.execute("SELECT COUNT(*) as count FROM files").fetchone()["count"]

        # Total photos and videos
        photos_count = conn.execute(
            "SELECT COUNT(*) as count FROM files WHERE file_type = 'photo'"
        ).fetchone()["count"]
        videos_count = conn.execute(
            "SELECT COUNT(*) as count FROM files WHERE file_type = 'video'"
        ).fetchone()["count"]

        # Total storage used
        total_storage = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) as total FROM files").fetchone()["total"]
        total_storage_mb = total_storage / (1024 * 1024)

        # Active shares
        active_shares = conn.execute(
            "SELECT COUNT(*) as count FROM shares WHERE is_complete = 1"
        ).fetchone()["count"]

        # Recent uploads (last 24 hours)
        recent_files = conn.execute(
            """SELECT COUNT(*) as count FROM files
               WHERE created_at >= datetime('now', '-24 hours')"""
        ).fetchone()["count"]

    finally:
        conn.close()

    return render_template(
        "admin/index.html",
        total_users=total_users,
        total_files=total_files,
        total_photos=photos_count,
        total_videos=videos_count,
        total_storage_mb=f"{total_storage_mb:.2f}",
        active_shares=active_shares,
        recent_files=recent_files,
    )


@admin_bp.route("/users")
@admin_required
def users():
    """View all registered users with pagination and search."""
    conn = get_db()
    try:
        # Search and filter parameters
        search_query = request.args.get("search", "").strip()
        page = request.args.get("page", 1, type=int)
        per_page = 20
        offset = (page - 1) * per_page

        # Build query with optional search filter
        base_query = """
            SELECT u.id, u.full_name, u.username, u.email,
                   u.created_at, COUNT(f.id) as file_count,
                   COALESCE(SUM(f.size_bytes), 0) as total_storage
            FROM users u
            LEFT JOIN shares s ON u.id = s.user_id
            LEFT JOIN files f ON s.id = f.share_id
        """

        # Get total count for pagination
        count_base = "SELECT COUNT(*) as count FROM users u"
        if search_query:
            count_query = count_base + """
                LEFT JOIN shares s ON u.id = s.user_id
                LEFT JOIN files f ON s.id = f.share_id
                WHERE u.username LIKE ? OR u.email LIKE ? OR u.full_name LIKE ?
            """
            search_pattern = f"%{search_query}%"
            total = conn.execute(count_query, (search_pattern, search_pattern, search_pattern)).fetchone()["count"]
            # Build query with search
            query = base_query + """
                WHERE u.username LIKE ? OR u.email LIKE ? OR u.full_name LIKE ?
                GROUP BY u.id
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
            """
            users_list = conn.execute(query, (search_pattern, search_pattern, search_pattern, per_page, offset)).fetchall()
        else:
            total = conn.execute(count_base).fetchone()["count"]
            # Build query without search
            query = base_query + """
                GROUP BY u.id
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
            """
            users_list = conn.execute(query, (per_page, offset)).fetchall()

        # Format storage for display (convert Row to mutable dict)
        users_list = [dict(user) for user in users_list]
        for user in users_list:
            user["total_storage_kb"] = user["total_storage"] / 1024

        # Create pagination object
        pagination = Pagination(
            page=page,
            per_page=per_page,
            total=total,
            record_name="users",
            show_single_page=False,
            format_number=True,
        )

    finally:
        conn.close()

    return render_template(
        "admin/users.html",
        users=users_list,
        pagination=pagination,
        search_query=search_query if search_query else "",
    )


@admin_bp.route("/files")
@admin_required
def files():
    """View all uploaded files with pagination, filters, and search."""
    conn = get_db()
    try:
        # Get filter parameters
        file_type = request.args.get("type", "").strip()  # photo, video, all
        page = request.args.get("page", 1, type=int)
        per_page = 20
        offset = (page - 1) * per_page
        search_query = request.args.get("search", "").strip()

        # Build base query
        base_query = """
            SELECT f.id, f.original_name, f.storage_key, f.content_type,
                   f.size_bytes, f.file_type, f.upload_status,
                   f.created_at, u.username as owner_username,
                   u.full_name as owner_name, s.token as share_token
            FROM files f
            JOIN shares s ON f.share_id = s.id
            JOIN users u ON s.user_id = u.id
        """

        # Get total count
        count_base = "SELECT COUNT(*) as count FROM files f JOIN shares s ON f.share_id = s.id JOIN users u ON s.user_id = u.id"
        if search_query:
            count_query = count_base + " WHERE f.original_name LIKE ?"
            if file_type and file_type != "all":
                count_query += " AND f.file_type = ?"
            search_pattern = f"%{search_query}%"
            if file_type and file_type != "all":
                total = conn.execute(count_query, (search_pattern, file_type)).fetchone()["count"]
            else:
                total = conn.execute(count_query, (search_pattern,)).fetchone()["count"]
        else:
            count_query = count_base
            if file_type and file_type != "all":
                count_query += " WHERE f.file_type = ?"
            if file_type and file_type != "all":
                total = conn.execute(count_query, (file_type,)).fetchone()["count"]
            else:
                total = conn.execute(count_query).fetchone()["count"]

        # Build full query with pagination
        if search_query:
            query = base_query + """
                WHERE f.original_name LIKE ?
            """
            if file_type and file_type != "all":
                query += " AND f.file_type = ?"
            query += """
                ORDER BY f.created_at DESC
                LIMIT ? OFFSET ?
            """
            if file_type and file_type != "all":
                files_list = conn.execute(
                    query, (f"%{search_query}%", file_type, per_page, offset)
                ).fetchall()
            else:
                files_list = conn.execute(
                    query, (f"%{search_query}%", per_page, offset)
                ).fetchall()
        else:
            query = base_query
            if file_type and file_type != "all":
                query += " WHERE f.file_type = ?"
            query += """
                ORDER BY f.created_at DESC
                LIMIT ? OFFSET ?
            """
            if file_type and file_type != "all":
                files_list = conn.execute(
                    query, (file_type, per_page, offset)
                ).fetchall()
            else:
                files_list = conn.execute(query, (per_page, offset)).fetchall()

        # Format file info for display (convert Row to mutable dict)
        files_list = [dict(file) for file in files_list]
        for file in files_list:
            file["size_kb"] = file["size_bytes"] / 1024
            file["size_mb"] = file["size_bytes"] / (1024 * 1024)

        # Create pagination object
        pagination = Pagination(
            page=page,
            per_page=per_page,
            total=total,
            record_name="files",
            show_single_page=False,
            format_number=True,
        )

    finally:
        conn.close()

    return render_template(
        "admin/files.html",
        files=files_list,
        pagination=pagination,
        current_type=file_type,
        search_query=search_query if search_query else "",
    )


@admin_bp.route("/files/<int:file_id>")
@admin_required
def file_detail(file_id):
    """View detailed information about a specific file."""
    conn = get_db()
    try:
        file = conn.execute(
            """SELECT f.*, u.username as owner_username, u.email as owner_email,
                      s.token as share_token, s.created_at as share_created_at,
                      s.expires_at as share_expires_at
               FROM files f
               JOIN shares s ON f.share_id = s.id
               JOIN users u ON s.user_id = u.id
               WHERE f.id = ?""",
            (file_id,)
        ).fetchone()

        if not file:
            abort(404)

        # Get S3 metadata if storage is available
        s3_info = None
        try:
            client = _get_client()
            response = client.head_object(
                Bucket=AppConfig.STORAGE_BUCKET,
                Key=file["storage_key"]
            )
            s3_info = {
                "content_type": response.get("ContentType"),
                "content_length": response.get("ContentLength"),
                "last_modified": response.get("LastModified"),
                "metadata": response.get("Metadata", {}),
            }
        except Exception as e:
            # Storage may not be available, but don't fail the request
            current_app.logger.debug("Could not retrieve S3 metadata for %s: %s", file["storage_key"], str(e))

        # Calculate share stats
        share = conn.execute(
            "SELECT * FROM shares WHERE id = ?",
            (file["share_id"],)
        ).fetchone()

        file_dict = dict(file)
        file_dict["size_kb"] = file_dict["size_bytes"] / 1024
        file_dict["size_mb"] = file_dict["size_bytes"] / (1024 * 1024)
        file = file_dict

    finally:
        conn.close()

    return render_template(
        "admin/file_detail.html",
        file=file,
        s3_info=s3_info,
        share=share,
    )


@admin_bp.route("/files/<int:file_id>/delete", methods=["POST"])
@admin_required
def delete_file_admin(file_id):
    """Delete a file from both database and storage."""
    conn = get_db()
    try:
        file = conn.execute(
            "SELECT * FROM files WHERE id = ?",
            (file_id,)
        ).fetchone()

        if not file:
            return jsonify({"error": "File not found"}), 404

        # Delete from storage
        try:
            from app.services.storage import delete_file
            delete_file(file["storage_key"])
        except Exception as e:
            current_app.logger.error("Failed to delete file from storage: %s", str(e))
            return jsonify({"error": "Failed to delete file from storage"}), 500

        # Delete from database
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()

        # Update share file count
        conn.execute(
            "UPDATE shares SET file_count = file_count - 1 WHERE id = ?",
            (file["share_id"],)
        )
        conn.commit()

        return jsonify({"status": "success"})

    finally:
        conn.close()


@admin_bp.route("/storage")
@admin_required
def storage():
    """View storage statistics and usage."""
    # Get total files from database
    conn = get_db()
    try:
        total_files = conn.execute("SELECT COUNT(*) as count FROM files").fetchone()["count"]
        total_size = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) as total FROM files").fetchone()["total"]

        # Photos and videos
        photos = conn.execute("SELECT COUNT(*) as count FROM files WHERE file_type = 'photo'").fetchone()["count"]
        videos = conn.execute("SELECT COUNT(*) as count FROM files WHERE file_type = 'video'").fetchone()["count"]

        # Storage by month (last 12 months)
        monthly_stats = conn.execute("""
            SELECT strftime('%Y-%m', created_at) as month,
                   COUNT(*) as file_count,
                   COALESCE(SUM(size_bytes), 0) as total_size
            FROM files
            WHERE created_at >= datetime('now', '-12 months')
            GROUP BY strftime('%Y-%m', created_at)
            ORDER BY month DESC
        """).fetchall()

    finally:
        conn.close()

    return render_template(
        "admin/storage.html",
        total_files=total_files,
        total_size_gb=total_size / (1024 ** 3),
        photos=photos,
        videos=videos,
        monthly_stats=monthly_stats,
    )


@admin_bp.route("/api/stats")
@admin_required
def api_stats():
    """API endpoint for admin statistics."""
    conn = get_db()
    try:
        stats = {
            "total_users": conn.execute("SELECT COUNT(*) as count FROM users").fetchone()["count"],
            "total_files": conn.execute("SELECT COUNT(*) as count FROM files").fetchone()["count"],
            "total_photos": conn.execute("SELECT COUNT(*) as count FROM files WHERE file_type = 'photo'").fetchone()["count"],
            "total_videos": conn.execute("SELECT COUNT(*) as count FROM files WHERE file_type = 'video'").fetchone()["count"],
            "total_storage_bytes": conn.execute("SELECT COALESCE(SUM(size_bytes), 0) as total FROM files").fetchone()["total"],
            "total_shares": conn.execute("SELECT COUNT(*) as count FROM shares WHERE is_complete = 1").fetchone()["count"],
        }
    finally:
        conn.close()

    return jsonify(stats)
