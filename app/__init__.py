"""
QualityShare — Flask Application Factory

This is the central setup file. It:
  1. Creates the Flask app
  2. Loads configuration
  3. Initializes the database
  4. Ensures the storage bucket exists
  5. Registers all route blueprints
"""

from flask import Flask, render_template
from app.config import Config
from app.models import init_db
from app.services.storage import ensure_bucket_exists


def create_app():
    """
    Build and return a configured Flask application.

    Call this from run.py or from a WSGI server like Gunicorn.
    """
    app = Flask(__name__)
    app.config.from_object(Config)

    # Increase max content length to support large chunk uploads.
    # Individual chunk size is enforced in the upload route;
    # this just prevents Flask from rejecting the raw request too early.
    # Set to chunk size + 1 MB overhead for form data.
    app.config["MAX_CONTENT_LENGTH"] = Config.CHUNK_SIZE_BYTES + 1_048_576

    # ── Database ──────────────────────────────────────────────────────────
    with app.app_context():
        init_db()

    # ── Storage bucket ────────────────────────────────────────────────────
    try:
        ensure_bucket_exists()
        app.logger.info("Storage bucket '%s' is ready.", Config.STORAGE_BUCKET)
    except Exception as e:
        app.logger.warning(
            "Could not connect to storage at %s: %s  "
            "(uploads will fail until storage is available)",
            Config.STORAGE_ENDPOINT,
            e,
        )

    
    
    

    # ── Blueprints ────────────────────────────────────────────────────────
    from app.routes.upload import upload_bp
    from app.routes.share import share_bp
    from app.routes.download import download_bp
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.admin import admin_bp

    app.register_blueprint(upload_bp)
    app.register_blueprint(share_bp)
    app.register_blueprint(download_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)

    # ── Homepage ─────────────────────────────────

    @app.route("/")
    def index():
        return render_template("upload.html")

    # ── Health check ──────────────────────────────────────────────────────
    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app
