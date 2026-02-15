import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

basedir = Path(__file__).resolve().parent.parent
load_dotenv(basedir / ".env")


def _as_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", f"sqlite:///{basedir / 'innovformation.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    ALLOWED_DOC_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"}

    PHOTO_UPLOAD_DIR = str(basedir / "app" / "static" / "uploads" / "photos")
    FORM_UPLOAD_DIR = str(basedir / "app" / "static" / "uploads" / "form_files")
    STUDENT_DOC_UPLOAD_DIR = str(basedir / "app" / "static" / "uploads" / "student_docs")

    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")

    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "eudyproject@gmail.com")
    SMTP_TLS = _as_bool("SMTP_TLS", True)

    # Session/cookie hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _as_bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = _as_bool("REMEMBER_COOKIE_SECURE", SESSION_COOKIE_SECURE)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # Reverse proxy / HTTPS / SEO
    PREFERRED_URL_SCHEME = os.getenv("PREFERRED_URL_SCHEME", "https")
    SECURITY_FORCE_HTTPS = _as_bool("SECURITY_FORCE_HTTPS", False)
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
