"""Local development entry point. cPanel/Passenger ignores this file.

Reads .env from this folder, then starts Flask's development server
with debug mode ON so you get clear error pages while learning.
Debug mode is for your machine only - the production file is
passenger_wsgi.py, which never enables debug.
"""
from dotenv import load_dotenv

load_dotenv()

# Import after load_dotenv so the app can already see DATABASE_URL etc.
from shortener import create_app  # noqa: E402

if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=True)
