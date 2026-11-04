"""
QualityShare — Entry Point

Run this file to start the development server:
    python run.py

In production, use Gunicorn instead:
    gunicorn "app:create_app()" --bind 0.0.0.0:5000
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
