from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# Local Python and Docker Compose use the same settings. Existing environment wins.
env_path = BASE_DIR / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

CACHE_ROOT = Path(os.getenv("CROSSMATCH_CACHE_ROOT", str(BASE_DIR / "cache"))).resolve()
DATABASE_ROOT = Path(os.getenv("CROSSMATCH_AUTH_DB_ROOT", str(BASE_DIR / "database"))).resolve()
CACHE_EXPIRATION_HOURS = max(1, int(os.getenv("CACHE_EXPIRATION_HOURS", "12")))
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "1")))
MAX_UPLOAD_GB = max(1, int(os.getenv("MAX_UPLOAD_GB", "25")))
CPU_WORKERS = max(0, int(os.getenv("CROSSMATCH_CPU_WORKERS", "0")))
MEMORY_PERCENT = max(25, min(95, int(os.getenv("CROSSMATCH_MEMORY_PERCENT", "80"))))
WEB_PORT = int(os.getenv("CROSSMATCH_HTTP_PORT", "18090"))
WEB_HOST = os.getenv("CROSSMATCH_HOST", "127.0.0.1")
AUTH_SESSION_HOURS = max(1, int(os.getenv("AUTH_SESSION_HOURS", "12")))
COOKIE_SECURE = os.getenv("CROSSMATCH_COOKIE_SECURE", "0") == "1"
APP_TITLE = "CrossMatch Browser"
CROSSMATCH_SETUP_KEY = os.getenv("CROSSMATCH_SETUP_KEY", "").strip()
APP_VERSION = "6.32.64"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}
