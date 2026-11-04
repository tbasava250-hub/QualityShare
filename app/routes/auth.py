"""
QualityShare Authentication Routes

Handles user registration, login, logout, and the @login_required decorator.
"""

from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from app.models import (
    create_user,
    get_user_by_email,
    get_user_by_username,
    get_user_by_id,
)

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
