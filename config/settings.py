"""
Django settings for the Pharma QA Reminder System.

All deployment-specific values (secrets, hosts, provider credentials) come from
environment variables. A local ``.env`` file is loaded when present; see
``.env.example`` for the full list.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-dev-only-key-do-not-use-in-production"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.sources",
    "apps.records",
    "apps.reminders",
    "apps.dashboards",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database: SQLite3 is mandatory for V1 -----------------------------------
# Keep the database on reliable local storage. WAL mode improves read/write
# concurrency but must NOT be enabled on network filesystems (NFS/SMB), which is
# why it is opt-in via SQLITE_WAL. See docs/OPERATIONS.md.
SQLITE_PATH = env("SQLITE_PATH", str(BASE_DIR / "data" / "db.sqlite3"))
_sqlite_init = ["PRAGMA foreign_keys=ON;", "PRAGMA busy_timeout=20000;"]
if env_bool("SQLITE_WAL", False):
    _sqlite_init += ["PRAGMA journal_mode=WAL;", "PRAGMA synchronous=NORMAL;"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": SQLITE_PATH,
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "".join(_sqlite_init),
        },
    }
}

CACHES = {
    # The database cache keeps throttling consistent across WSGI workers.
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Default business timezone used for "today" when evaluating reminder dates.
# Admins can override it in System Settings.
BUSINESS_TIMEZONE = env("BUSINESS_TIMEZONE", "Asia/Kolkata")

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Same-server deployment (e.g. Windows server): Django also serves the built frontend from this
# folder at "/", so the app and the API share one address and no CORS is needed.
FRONTEND_DIST = Path(env("FRONTEND_DIST") or str(BASE_DIR / "frontend_dist"))
if (FRONTEND_DIST / "index.html").exists():
    WHITENOISE_ROOT = str(FRONTEND_DIST)
    WHITENOISE_INDEX_FILE = True

# Uploaded source workbooks live outside any web-served directory.
PRIVATE_UPLOAD_ROOT = Path(env("PRIVATE_UPLOAD_ROOT", str(BASE_DIR / "data" / "uploads")))
BACKUP_ROOT = Path(env("BACKUP_ROOT", str(BASE_DIR / "data" / "backups")))
MAX_WORKBOOK_BYTES = int(env("MAX_WORKBOOK_BYTES", str(25 * 1024 * 1024)))
MAX_WORKBOOK_ROWS = int(env("MAX_WORKBOOK_ROWS", "50000"))
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_WORKBOOK_BYTES + 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Server-side folders that may be used as data sources: "name=/abs/path;name2=/abs/path2".
# Only paths under these roots can ever be read (path traversal is rejected).
SOURCE_ROOTS = {}
for _item in env("SOURCE_ROOTS", "").split(";"):
    if "=" in _item:
        _name, _path = _item.split("=", 1)
        if _name.strip() and _path.strip():
            SOURCE_ROOTS[_name.strip()] = _path.strip()

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication ------------------------------------------------------------
AUTH_TOKEN_TTL_HOURS = int(env("AUTH_TOKEN_TTL_HOURS", "10"))
LOGIN_MAX_FAILURES = int(env("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCKOUT_MINUTES = int(env("LOGIN_LOCKOUT_MINUTES", "15"))

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.ExpiringTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": env("THROTTLE_USER", "600/min"),
        "anon": env("THROTTLE_ANON", "60/min"),
        "login": env("THROTTLE_LOGIN", "10/min"),
        "sensitive": env("THROTTLE_SENSITIVE", "20/min"),
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.safe_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# --- CORS (GitHub Pages / separate frontend origin) ----------------------------
# Auth uses an Authorization header, not cookies, so credentials are not needed.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
CORS_ALLOW_CREDENTIALS = False
CORS_URLS_REGEX = r"^/api/.*$"
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

# --- Security hardening ----------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
if not DEBUG and not TESTING:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "0"))
SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]

ENABLE_DJANGO_ADMIN = env_bool("ENABLE_DJANGO_ADMIN", False)

# Secret for POST /api/jobs/tick/ (external cron trigger). Empty disables the endpoint.
JOB_TRIGGER_TOKEN = env("JOB_TRIGGER_TOKEN", "")

# Frontend base URL, used for links in emails (optional).
FRONTEND_URL = env("FRONTEND_URL", "")

# --- Logging -------------------------------------------------------------------
LOG_DIR = Path(env("LOG_DIR", str(BASE_DIR / "logs")))
LOG_LEVEL = env("LOG_LEVEL", "INFO")
_handlers = ["console"]
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "apps.core.logging.JsonFormatter"},
    },
    "filters": {
        "redact": {"()": "apps.core.logging.RedactSecretsFilter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["redact"]},
    },
    "root": {"handlers": _handlers, "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": _handlers, "level": "WARNING", "propagate": False},
        "qa": {"handlers": _handlers, "level": LOG_LEVEL, "propagate": False},
    },
}
if env_bool("LOG_TO_FILE", False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOGGING["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(LOG_DIR / "app.log"),
        "maxBytes": 5 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "json",
        "filters": ["redact"],
    }
    for _logger in [LOGGING["root"], LOGGING["loggers"]["django.request"], LOGGING["loggers"]["qa"]]:
        _logger["handlers"] = _handlers + ["file"]

if TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LOGGING["root"]["level"] = "CRITICAL"
    LOGGING["loggers"]["qa"]["level"] = "CRITICAL"
    LOGGING["loggers"]["django.request"]["level"] = "CRITICAL"

Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
