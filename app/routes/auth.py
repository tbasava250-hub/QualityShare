"""
QualityShare Authentication Routes

Handles user registration, login, logout, and the @login_required decorator.
"""

import uuid
from functools import wraps
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from app.models import (
    create_user,
    get_user_by_email,
    get_user_by_username,
    get_user_by_id,
    set_password_reset_token,
    get_user_by_reset_token,
    update_user_password,
)
from app.config import Config
from app.services.email_service import send_password_reset_email

auth_bp = Blueprint("auth", __name__)


def login_required(f):
    """Decorator to protect routes that require authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required."}), 401
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """User registration page and handler."""
    if "user_id" in session:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form

        full_name = data.get("full_name", "").strip()
        username = data.get("username", "").strip()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        confirm_password = data.get("confirm_password", "")

        # Validation
        if not full_name or not username or not email or not password:
            error = "All fields are required."
            if request.is_json:
                return jsonify({"error": error}), 400
            flash(error, "danger")
            return render_template("register.html")

        if password != confirm_password:
            error = "Passwords do not match."
            if request.is_json:
                return jsonify({"error": error}), 400
            flash(error, "danger")
            return render_template("register.html")

        if len(password) < 6:
            error = "Password must be at least 6 characters long."
            if request.is_json:
                return jsonify({"error": error}), 400
            flash(error, "danger")
            return render_template("register.html")

        # Check uniqueness
        if get_user_by_username(username):
            error = "Username is already taken."
            if request.is_json:
                return jsonify({"error": error}), 400
            flash(error, "danger")
            return render_template("register.html")

        if get_user_by_email(email):
            error = "Email address is already registered."
            if request.is_json:
                return jsonify({"error": error}), 400
            flash(error, "danger")
            return render_template("register.html")

        # Create user with secure password hash
        password_hash = generate_password_hash(password)
        try:
            user_id = create_user(full_name, username, email, password_hash)
            session["user_id"] = user_id
            session["username"] = username
            session["full_name"] = full_name

            if request.is_json:
                return jsonify({"status": "success", "redirect": url_for("dashboard.index")}), 201

            flash("Account created successfully! Welcome to your dashboard.", "success")
            return redirect(url_for("dashboard.index"))
        except Exception as e:
            error = f"Registration failed: {str(e)}"
            if request.is_json:
                return jsonify({"error": error}), 500
            flash(error, "danger")

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """User login page and handler."""
    if "user_id" in session:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form

        login_id = data.get("login_id", "").strip()  # email or username
        password = data.get("password", "")

        if not login_id or not password:
            error = "Please enter your username/email and password."
            if request.is_json:
                return jsonify({"error": error}), 400
            flash(error, "danger")
            return render_template("login.html")

        # Find user by email or username
        user = get_user_by_email(login_id.lower())
        if not user:
            user = get_user_by_username(login_id)

        if not user or not check_password_hash(user["password_hash"], password):
            error = "Invalid email/username or password."
            if request.is_json:
                return jsonify({"error": error}), 401
            flash(error, "danger")
            return render_template("login.html")

        # Login successful
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["full_name"] = user["full_name"]

        if request.is_json:
            return jsonify({"status": "success", "redirect": url_for("dashboard.index")}), 200

        flash(f"Welcome back, {user['full_name']}!", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    """Log out the current user by clearing the session."""
    session.clear()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Forgot password request page and handler."""
    if "user_id" in session:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your email address.", "danger")
            return render_template("forgot_password.html")

        user = get_user_by_email(email)
        # Prevent account enumeration: show neutral confirmation whether user exists or not
        if user:
            token = uuid.uuid4().hex
            expiry_minutes = Config.PASSWORD_RESET_EXPIRY_MINUTES
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)).strftime("%Y-%m-%d %H:%M:%S")
            set_password_reset_token(email, token, expires_at)

            reset_url = f"{Config.BASE_URL}/reset-password/{token}"

            # Send email using the email service
            # Never reveal whether email was sent successfully - always show generic message
            email_sent = send_password_reset_email(
                recipient_email=email,
                reset_url=reset_url,
                expiry_minutes=expiry_minutes
            )

            if not email_sent:
                current_app.logger.info(
                    "Password reset requested for %s, email not configured or failed",
                    email
                )

        flash("If that email address is registered, a password reset link has been sent.", "success")
        return render_template("forgot_password.html")

    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Reset password page and handler using token."""
    if "user_id" in session:
        return redirect(url_for("dashboard.index"))

    user = get_user_by_reset_token(token)
    if not user:
        flash("Invalid or expired password reset link.", "danger")
        return redirect(url_for("auth.forgot_password"))

    # Check expiration
    if user["reset_expires"]:
        try:
            expires_at = datetime.strptime(user["reset_expires"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                flash("This password reset link has expired. Please request a new one.", "danger")
                return redirect(url_for("auth.forgot_password"))
        except ValueError:
            pass

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password or password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html", token=token)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("reset_password.html", token=token)

        password_hash = generate_password_hash(password)
        update_user_password(user["id"], password_hash)

        flash("Your password has been successfully reset. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)
