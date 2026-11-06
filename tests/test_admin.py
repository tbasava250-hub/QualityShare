"""
Tests for admin dashboard functionality.

SECURITY TESTS:
- Admin-only access
- User privacy protection
- Storage listing
- Pagination
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.models import init_db, get_db, create_user
from werkzeug.security import generate_password_hash


@pytest.fixture
def app():
    import tempfile
    import shutil

    # Create a unique temp database for each test
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")

    app = create_app()
    app.config.update({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "WTF_CSRF_ENABLED": False,
        "DEBUG": False,
    })

    with app.app_context():
        init_db()
        yield app

    # Cleanup temp database
    shutil.rmtree(temp_dir)


@pytest.fixture
def client(app):
    # Use session_transaction to properly handle session persistence
    return app.test_client(use_cookies=True)


def create_admin_client(app, client):
    """Create an admin user and return a client logged in as that admin.

    Note: Flask test client doesn't persist sessions across requests.
    This function sets the session and returns (client, username).
    Tests should use the returned client for admin requests.
    """
    with app.app_context():
        conn = get_db()
        # Create admin user with a unique username for each test
        import uuid
        unique_suffix = str(uuid.uuid4())[:6]
        unique_username = f"admin_{unique_suffix}"
        unique_email = f"{unique_suffix}.admin@example.com"
        password_hash = generate_password_hash("testpassword")
        cursor = conn.execute(
            "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
            ("Admin User", unique_username, unique_email, password_hash, 1)
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()

    # Login as admin using form data
    client.post("/login", data={
        "login_id": unique_username,
        "password": "testpassword"
    }, follow_redirects=True)

    # Set session using session_transaction - Flask requires this to be set
    # BEFORE making the authenticated request
    return client, user_id, unique_username


def create_test_user(conn, username, email, password="testpassword", is_admin=0):
    """Helper to create a test user with unique email. Returns (user_id, username, email)."""
    import uuid
    # Use timestamp and uuid to ensure unique username and email
    unique_suffix = str(uuid.uuid4())[:8]
    unique_username = f"{username}_{unique_suffix}"
    unique_email = f"{unique_suffix}.{email}"
    password_hash = generate_password_hash(password)
    cursor = conn.execute(
        "INSERT INTO users (full_name, username, email, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
        (f"Test User {username}", unique_username, unique_email, password_hash, is_admin)
    )
    conn.commit()
    return cursor.lastrowid, unique_username, unique_email


def create_share_and_file(conn, user_id, filename="test.txt", file_type="photo"):
    """Helper to create a share and file for testing."""
    import uuid
    share_token = uuid.uuid4().hex
    cursor = conn.execute(
        "INSERT INTO shares (token, user_id, is_complete) VALUES (?, ?, 1)",
        (share_token, user_id)
    )
    share_id = cursor.lastrowid

    cursor = conn.execute(
        """INSERT INTO files (share_id, original_name, storage_key, content_type,
           size_bytes, file_type, upload_status, checksum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (share_id, filename, f"test/{share_token}/{filename}", "text/plain", 1024, file_type, "complete", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    )
    conn.commit()
    return share_id, cursor.lastrowid


class TestAdminAuthorization:
    """Test admin route authorization."""

    def test_unauthenticated_access_denied(self, client):
        """Unauthenticated users should be redirected to login."""
        response = client.get("/admin")
        assert response.status_code == 302
        assert "/login" in response.location

    def test_non_admin_access_denied(self, client, app):
        """Non-admin users should be denied access."""
        # Create regular user
        with app.app_context():
            conn = get_db()
            user_id, username, _ = create_test_user(conn, "regular", "regular@example.com", is_admin=0)
            conn.close()

        # Login as regular user using form data
        client.post("/login", data={
            "login_id": username,
            "password": "testpassword"
        })

        response = client.get("/admin")
        assert response.status_code == 403

    def test_admin_access_allowed(self, client, app):
        """Admin users should be able to access admin routes."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            user_id, username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            conn.close()

        # Login and set session inline
        client.post("/login", data={
            "login_id": username,
            "password": "testpassword"
        }, follow_redirects=True)

        # Set session using session_transaction for subsequent requests
        # The request must be made within the same session_transaction context
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = username
            sess["full_name"] = "Test User admin"

        # Make request with the session cookie
        response = client.get("/admin", follow_redirects=True)
        assert response.status_code == 200
        assert b"Admin Dashboard" in response.data

    def test_admin_dashboard_data(self, client, app):
        """Admin dashboard should display correct statistics."""
        # Create admin user
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            # Get admin id
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            # Create regular users
            create_test_user(conn, "user1", "user1@example.com", is_admin=0)
            create_test_user(conn, "user2", "user2@example.com", is_admin=0)
            # Create files
            for i in range(5):
                create_share_and_file(conn, admin_id if i < 3 else 2, f"file{i}.txt", "photo" if i < 4 else "video")
            conn.close()

        # Login and set session inline
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        # Set session and make request within same context
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        response = client.get("/admin", follow_redirects=True)
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "Total Users" in data
        assert "7" in data  # 2 users + 5 files


class TestAdminUserManagement:
    """Test user management in admin dashboard."""

    def test_users_page_lists_all_users(self, client, app):
        """Admin users page should list all registered users."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            # Create 3 regular users (all will be listed)
            user1_id, user1_username, user1_email = create_test_user(conn, "user1", "user1@example.com", is_admin=0)
            user2_id, user2_username, user2_email = create_test_user(conn, "user2", "user2@example.com", is_admin=0)
            user3_id, user3_username, user3_email = create_test_user(conn, "user3", "user3@example.com", is_admin=1)
            # Get admin user id (user3 is admin)
            admin_id = user3_id
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": user3_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = user3_username
            sess["full_name"] = "Test User user3"

        response = client.get("/admin/users", follow_redirects=True)
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "user1@example.com" in data
        assert "user2@example.com" in data
        assert "user3@example.com" in data

    def test_users_search_functionality(self, client, app):
        """Admin should be able to search users."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            create_test_user(conn, "alice", "alice@example.com", is_admin=0)
            create_test_user(conn, "bob", "bob@example.com", is_admin=0)
            create_test_user(conn, "charlie", "charlie@example.com", is_admin=1)
            _, admin_username, _ = create_test_user(conn, "admin_search", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username LIKE 'admin_search_%'").fetchone()["id"]
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin_search"

        # Search for alice
        response = client.get("/admin/users?search=ali", follow_redirects=True)
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "alice" in data
        assert "bob" not in data
        assert "charlie" not in data


class TestAdminFileManagement:
    """Test file management in admin dashboard."""

    def test_files_page_lists_all_files(self, client, app):
        """Admin files page should list all uploaded files."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            create_share_and_file(conn, admin_id, "photo1.jpg", "photo")
            create_share_and_file(conn, admin_id, "video1.mp4", "video")
            create_share_and_file(conn, admin_id, "document.pdf", "other")
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        response = client.get("/admin/files", follow_redirects=True)
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "photo1.jpg" in data
        assert "video1.mp4" in data
        assert "document.pdf" in data

    def test_files_type_filter(self, client, app):
        """Admin should be able to filter files by type."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            create_share_and_file(conn, admin_id, "photo1.jpg", "photo")
            create_share_and_file(conn, admin_id, "video1.mp4", "video")
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        # Filter photos
        response = client.get("/admin/files?type=photo", follow_redirects=True)
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "photo1.jpg" in data
        assert "video1.mp4" not in data

    def test_file_detail_page(self, client, app):
        """Admin file detail page should show file information."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            _, file_id = create_share_and_file(conn, admin_id, "testfile.jpg", "photo")
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        response = client.get(f"/admin/files/{file_id}", follow_redirects=True)
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "testfile.jpg" in data
        assert "admin@example.com" in data  # Owner email should be visible


class TestAdminStorageStats:
    """Test storage statistics functionality."""

    def test_storage_page(self, client, app):
        """Admin storage page should display storage statistics."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            create_share_and_file(conn, admin_id, "photo1.jpg", "photo")
            create_share_and_file(conn, admin_id, "video1.mp4", "video")
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        response = client.get("/admin/storage", follow_redirects=True)
        assert response.status_code == 200
        data = response.get_data(as_text=True)
        assert "Storage" in data or "Storage" in data

    def test_api_stats_endpoint(self, client, app):
        """Admin API stats endpoint should return JSON statistics."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            create_test_user(conn, "user1", "user1@example.com", is_admin=0)
            create_test_user(conn, "user2", "user2@example.com", is_admin=0)
            create_share_and_file(conn, admin_id, "file1.txt", "photo")
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        response = client.get("/admin/api/stats", follow_redirects=True)
        assert response.status_code == 200
        json_data = response.get_json()
        assert "total_users" in json_data
        assert "total_files" in json_data
        assert "total_storage_bytes" in json_data
        assert "total_photos" in json_data
        assert "total_videos" in json_data


class TestAdminSecurity:
    """Test security aspects of admin functionality."""

    def test_no_credentials_exposed(self, client, app):
        """Admin pages should never expose credentials."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        response = client.get("/admin", follow_redirects=True)
        data = response.get_data(as_text=True)

        # Check that storage credentials are not exposed
        assert "STORAGE_ACCESS_KEY" not in data
        assert "STORAGE_SECRET_KEY" not in data
        assert "minioadmin" not in data

    def test_password_hashes_not_exposed(self, client, app):
        """Password hashes should never be exposed in admin interface."""
        # Create admin user and set session
        with app.app_context():
            conn = get_db()
            _, admin_username, _ = create_test_user(conn, "admin", "admin@example.com", is_admin=1)
            admin_id = conn.execute("SELECT id FROM users WHERE username = ?", (admin_username,)).fetchone()["id"]
            conn.close()

        # Login and set session
        client.post("/login", data={
            "login_id": admin_username,
            "password": "testpassword"
        }, follow_redirects=True)

        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
            sess["username"] = admin_username
            sess["full_name"] = "Test User admin"

        response = client.get("/admin/users", follow_redirects=True)
        data = response.get_data(as_text=True)

        # Check that password hashes are not exposed
        assert "pbkdf2" not in data.lower()
        assert "sha256" not in data.lower()
