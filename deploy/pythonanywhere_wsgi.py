# PythonAnywhere WSGI configuration.
# Copy into the WSGI file linked from the "Web" tab (e.g. /var/www/<username>_pythonanywhere_com_wsgi.py)
# and replace <username> and the project path.
import os
import sys

PROJECT_DIR = "/home/<username>/Pharma-advanced-backend"
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# Settings load PROJECT_DIR/.env automatically (python-dotenv).

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
