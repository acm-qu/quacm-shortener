"""Entry point for cPanel's "Setup Python App" (Phusion Passenger).

Passenger imports this file and uses the WSGI ``application`` object.
No development server and no debug mode here - that is production rule #1.
Locally you use run_local.py instead; cPanel uses this file.
"""
import os
import sys

# Make sure the folder containing this file is on Python's import path,
# so "import shortener" works no matter how Passenger starts us.
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

# If a .env file was uploaded next to this file, read it. The better
# habit on cPanel is to set variables in the "Environment Variables"
# screen of the Setup Python App page (keeps secrets out of the web folder).
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(APP_ROOT, ".env"))
except ImportError:
    pass

from shortener import create_app

application = create_app()
